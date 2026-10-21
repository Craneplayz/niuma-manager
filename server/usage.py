"""Content-free usage counters, voluntary feedback and administrator aggregates."""
import hashlib
import json
import os
import secrets
import math
from statistics import median
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
if __package__:
    from . import error_reports
else:
    import error_reports

TZ = timezone(timedelta(hours=8))
OPERATIONS = {
    '/api/sources/sync': 'sync', '/api/recognition/retry': 'recognition',
    '/api/recognition/batches': 'recognition', '/api/planning/proposals': 'planning',
    '/api/planning/apply': 'plan_apply', '/api/decomposition/proposals': 'decomposition',
}

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS usage_events(id TEXT PRIMARY KEY, actor TEXT NOT NULL,
        occurred_at TEXT NOT NULL, day TEXT NOT NULL, event TEXT NOT NULL,
        success INTEGER NOT NULL, duration_ms INTEGER NOT NULL, quantity INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS usage_day ON usage_events(day,event);
    CREATE TABLE IF NOT EXISTS usage_visits(actor TEXT PRIMARY KEY, first_day TEXT NOT NULL,
        last_day TEXT NOT NULL, rating_offered INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS usage_feedback(id TEXT PRIMARY KEY, actor TEXT NOT NULL,
        created_at TEXT NOT NULL, kind TEXT NOT NULL, score INTEGER, message TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS usage_one_rating ON usage_feedback(actor) WHERE kind='rating';
    CREATE TABLE IF NOT EXISTS usage_admin_audit(id TEXT PRIMARY KEY, actor TEXT NOT NULL,
        created_at TEXT NOT NULL, action TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS usage_admins(actor TEXT PRIMARY KEY);
    ''')

def actor(uid):
    return hashlib.sha256(('niuma-usage:' + uid).encode()).hexdigest()

def admin(uid, db):
    # Explicit server-side IDs only; registration/profile cannot grant this role.
    return uid in os.environ.get('NIUMA_ADMIN_USER_IDS', '').split() or bool(db.execute('SELECT 1 FROM usage_admins WHERE actor=?',(actor(uid),)).fetchone())

def operation(path):
    if path.startswith('/api/recognition/candidates/') and path.endswith('/resolve'): return 'recognition_resolve'
    return OPERATIONS.get(path)

def successful(status, value, request_id=None):
    if status>=400: return False
    if isinstance(value,list):
        selected=[r for r in value if isinstance(r,dict) and r.get('request',{}).get('request_id')==request_id]
        return not any(r.get('result',{}).get('status')=='failed' for r in selected)
    return not (isinstance(value,dict) and value.get('status')=='failed')

def record(db, uid, event, success=True, duration_ms=0, quantity=1, now=None):
    now = now or datetime.now(TZ)
    db.execute('INSERT INTO usage_events VALUES(?,?,?,?,?,?,?,?)',
        (secrets.token_hex(16),actor(uid),now.isoformat(),now.date().isoformat(),event,
         int(success),max(0,round(duration_ms)),max(0,quantity)))
    cutoff = (now - timedelta(days=90)).date().isoformat()
    db.execute('DELETE FROM usage_events WHERE day<?',(cutoff,))
    db.execute('DELETE FROM usage_feedback WHERE substr(created_at,1,10)<?',(cutoff,))

def visit(db, uid, now=None):
    now = now or datetime.now(TZ)
    today, yesterday = now.date().isoformat(), (now-timedelta(days=1)).date().isoformat()
    key = actor(uid)
    db.execute('BEGIN IMMEDIATE')
    old = db.execute('SELECT * FROM usage_visits WHERE actor=?',(key,)).fetchone()
    offer = bool(old and old['last_day']==yesterday and not old['rating_offered'])
    if old:
        db.execute('UPDATE usage_visits SET last_day=?,rating_offered=max(rating_offered,?) WHERE actor=?',
            (today,int(offer),key))
    else:
        db.execute('INSERT INTO usage_visits VALUES(?,?,?,0)',(key,today,today))
    # One visit per actor/day, not every page refresh.
    if not old or old['last_day']!=today: record(db,uid,'visit',now=now)
    return dict(offerRating=offer)

def workspace_changes(db, uid, old, new):
    old = [t for t in old if t.get('content_type')!='notice']
    new = [t for t in new if t.get('content_type')!='notice']
    before = {t['id']:t for t in old}
    added = sum(t['id'] not in before for t in new)
    completed = sum(t.get('status')=='completed' and before.get(t['id'],{}).get('status')!='completed' for t in new)
    edited = sum(t['id'] in before and t!=before[t['id']] for t in new)
    for event,n in [('task_created',added),('task_completed',completed),('task_edited',edited)]:
        if n: record(db,uid,event,quantity=n)
    dated,on_time=0,0
    now=datetime.now(TZ)
    for task in new:
        if task.get('status')!='completed' or before.get(task['id'],{}).get('status')=='completed' or not task.get('due'): continue
        due=task['due']
        if not isinstance(due,dict):continue
        try: deadline=datetime.fromisoformat(due['date']+'T'+(due.get('time') or '23:59:59')+'+08:00')
        except (ValueError,KeyError,TypeError):continue
        dated+=1
        on_time+=now<=deadline
    if dated: record(db,uid,'dated_task_completed',quantity=dated)
    if on_time: record(db,uid,'task_on_time',quantity=on_time)

def summary(db, start, end):
    rows = [dict(r) for r in db.execute('SELECT * FROM usage_events WHERE day BETWEEN ? AND ?',(start,end))]
    excluded = {actor(uid) for uid in (os.environ.get('NIUMA_ANALYTICS_EXCLUDED_USER_IDS','')+' '+os.environ.get('NIUMA_ADMIN_USER_IDS','')).split()}
    excluded.update(r['actor'] for r in db.execute('SELECT actor FROM usage_admins'))
    rows = [r for r in rows if r['actor'] not in excluded]
    by_day, by_event = {}, {}
    for r in rows:
        by_day.setdefault(r['day'],set()).add(r['actor'])
        by_event.setdefault(r['event'],[]).append(r)
    metrics = []
    for name, values in sorted(by_event.items()):
        durations = sorted(r['duration_ms'] for r in values)
        metrics.append(dict(event=name,count=len(values),quantity=sum(r['quantity'] for r in values),
            users=len({r['actor'] for r in values}),success=sum(r['success'] for r in values),
            medianMs=median(durations),p95Ms=durations[math.ceil(len(durations)*.95)-1]))
    feedback = [dict(r) for r in db.execute('SELECT actor,created_at,kind,score,message FROM usage_feedback WHERE substr(created_at,1,10) BETWEEN ? AND ? ORDER BY created_at DESC',(start,end)) if r['actor'] not in excluded]
    scores=[r['score'] for r in feedback if r['kind']=='rating']
    rating=dict(samples=len(scores),average=round(sum(scores)/len(scores),2) if scores else None)
    feedback=feedback[:100]
    for row in feedback: row.pop('actor')
    return dict(start=start,end=end,activeUsers=len({r['actor'] for r in rows}),
        daily=[dict(day=day,users=len(users)) for day,users in sorted(by_day.items())],
        metrics=metrics,feedback=feedback,rating=rating,environment=os.environ.get('NIUMA_ENVIRONMENT','development'),
        methodology='从启用记录后统计；流程量不是同批次转化漏斗；耗时含排队，中位数为常规样本中位数，P95使用最近秩 ceil(0.95×n)；不推断历史行为或模型正确率。')

def route(handler, db, uid, path, body, error):
    if path=='/api/usage/visit' and handler.command=='POST':
        return handler.reply(200,visit(db,uid)) or True
    if path=='/api/usage/feedback' and handler.command=='POST':
        kind,score,message = body.get('kind'),body.get('score'),body.get('message','')
        if kind not in ('feedback','rating') or not isinstance(message,str) or len(message)>2000:
            raise error(400,'反馈格式不正确')
        if kind=='rating' and (type(score)!=int or not 1<=score<=5): raise error(400,'请选择1至5分')
        if kind=='feedback' and (not message.strip() or score is not None): raise error(400,'请填写反馈')
        db.execute('''INSERT INTO usage_feedback VALUES(?,?,?,?,?,?)
            ON CONFLICT(actor) WHERE kind='rating' DO UPDATE SET
                score=excluded.score,created_at=excluded.created_at,message=excluded.message''',
            (secrets.token_hex(16),actor(uid),datetime.now(TZ).isoformat(),kind,score,message.strip()))
        return handler.reply(200,dict(saved=True)) or True
    if path.startswith('/api/admin/'):
        if not admin(uid,db): raise error(403,'此账号没有后台访问权限')
        if path=='/api/admin/operations' and handler.command=='GET':
            from server import operations
            query=parse_qs(urlsplit(handler.path).query)
            now=datetime.now(TZ)
            try:
                start=datetime.strptime(query['start'][0],'%Y-%m-%d').replace(tzinfo=TZ) if 'start' in query else now-timedelta(days=1)
                end=datetime.strptime(query['end'][0],'%Y-%m-%d').replace(tzinfo=TZ)+timedelta(days=1) if 'end' in query else now
                if start>=end or (end-start).days>90:raise ValueError()
            except (ValueError,KeyError):raise error(400,'请选择不超过90天的有效日期范围')
            result=operations.summary(handler.media_db_path,db,start.timestamp(),end.timestamp())
            return handler.reply(200,result) or True
        if path!='/api/admin/usage' or handler.command!='GET': raise error(404,'接口不存在')
        query=parse_qs(urlsplit(handler.path).query)
        today=datetime.now(TZ).date()
        try:
            start=datetime.strptime(query.get('start',[(today-timedelta(days=29)).isoformat()])[0],'%Y-%m-%d').date()
            end=datetime.strptime(query.get('end',[today.isoformat()])[0],'%Y-%m-%d').date()
            if start>end or (end-start).days>89: raise ValueError()
        except ValueError: raise error(400,'请选择不超过90天的有效日期范围')
        result=summary(db,start.isoformat(),end.isoformat())
        result['errorReports']=error_reports.recent(db,start.isoformat(),end.isoformat())
        db.execute('INSERT INTO usage_admin_audit VALUES(?,?,?,?)',(secrets.token_hex(16),actor(uid),datetime.now(TZ).isoformat(),'usage_read'))
        return handler.reply(200,result) or True
    return False

if __name__=='__main__':
    import argparse
    import sqlite3
    from pathlib import Path
    parser=argparse.ArgumentParser(description='Server-only administrator grant/revoke; no public role mutation API')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--grant');group.add_argument('--revoke')
    parser.add_argument('--db',type=Path,required=True)
    args=parser.parse_args();uid=args.grant or args.revoke
    with sqlite3.connect(args.db.resolve().as_uri()+'?mode=rw',uri=True) as db:
        if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():parser.error('user ID not found')
        if args.grant:db.execute('INSERT OR IGNORE INTO usage_admins VALUES(?)',(actor(uid),))
        else:db.execute('DELETE FROM usage_admins WHERE actor=?',(actor(uid),))
    print('Administrator permission updated; account contents unchanged.')
