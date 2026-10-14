"""Account-scoped public lecture previews and explicit, idempotent notice import."""
import hashlib
from contextlib import closing
import json
import secrets
import sqlite3
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from jsonschema import ValidationError

from contracts import public_import as contract
from source_sync import CRAWLER

ORIGINS={'cs':'cs.pku.edu.cn','bio':'web.bio.pku.edu.cn','gse':'www.gse.pku.edu.cn'}
_lock=threading.Lock();_workers=set()


def initialize(db,recover=False):
    db.execute('''CREATE TABLE IF NOT EXISTS public_source_runs(
      id TEXT PRIMARY KEY,user_id TEXT NOT NULL,operation_id TEXT NOT NULL,source TEXT NOT NULL,state TEXT NOT NULL,
      result TEXT,error TEXT,created_at TEXT NOT NULL,UNIQUE(user_id,operation_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS public_source_imports(
      user_id TEXT NOT NULL,event_id TEXT NOT NULL,task_id TEXT NOT NULL,PRIMARY KEY(user_id,event_id))''')
    if recover:db.execute("UPDATE public_source_runs SET state='failed',error='服务重启中断了采集，请重新获取' WHERE state='running'")


def view(row):
    return contract.check('run',dict(contract_version=contract.VERSION,run_id=row['id'],source_id=row['source'],state=row['state'],
                result=json.loads(row['result']) if row['result'] else None,error=row['error'],created_at=row['created_at']))


def collect(source):
    if not (CRAWLER/'node_modules'/'tsx').exists():raise ValueError('请安装 crawler 采集依赖')
    with tempfile.TemporaryDirectory(prefix='niuma-public-') as directory:
        done=subprocess.run(['node','--import','tsx','src/public-import-cli.ts',source,directory],cwd=CRAWLER,
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf-8',timeout=150)
        if done.returncode:raise ValueError('公开网站暂时不可用，请稍后重试')
        if len(done.stdout)>3_000_000:raise ValueError('采集内容超过大小限制')
        result=contract.check('capture',json.loads(done.stdout))
        for event in result['events']:
            parsed=urlsplit(event['url'])
            if event['source_id']!=source or parsed.scheme!='https' or parsed.hostname!=ORIGINS[source] or parsed.username or parsed.password:
                raise ValueError('采集结果来源不匹配')
        return result


def process(path,uid,run_id,source):
    try:
        try:result=collect(source);state='succeeded';error=None
        except Exception:result=None;state='failed';error='公开网站采集未完成，请检查网络和采集依赖后重试。'
        with closing(sqlite3.connect(path,timeout=10)) as db, db:
            db.execute('UPDATE public_source_runs SET state=?,result=?,error=? WHERE id=? AND user_id=?',
                       (state,json.dumps(result) if result else None,error,run_id,uid))
    finally:
        with _lock:_workers.discard((str(path),run_id))


def launch(path,uid,row):
    key=(str(path),row['id'])
    with _lock:
        if key in _workers:return
        _workers.add(key)
    threading.Thread(target=process,args=(path,uid,row['id'],row['source']),daemon=True).start()


def route(handler,db,uid,path,body,ApiError,database):
    root='/api/sources/public'
    if path!=root and not path.startswith(root+'/'):return False
    if path==root+'/catalog' and handler.command=='GET':
        handler.reply(200,dict(contract_version=contract.VERSION,sources=[dict(id=k,label=v) for k,v in contract.SOURCES.items()]));return True
    if path==root+'/runs':
        if handler.command=='GET':
            rows=db.execute('SELECT * FROM public_source_runs WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 10',(uid,)).fetchall()
            handler.reply(200,dict(contract_version=contract.VERSION,runs=[view(r) for r in rows]));return True
        if handler.command=='POST':
            try:contract.check('start',body)
            except (ValueError,ValidationError):raise ApiError(400,'请选择支持的公开网站') from None
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM public_source_runs WHERE user_id=? AND operation_id=?',(uid,body['operation_id'])).fetchone()
            if row and row['source']!=body['source_id']:raise ApiError(409,'操作编号已用于另一个来源')
            if not row:
                active=db.execute("SELECT * FROM public_source_runs WHERE user_id=? AND state='running'",(uid,)).fetchone()
                if active:raise ApiError(409,'公开网站仍在采集中，请等待完成')
                if db.execute("SELECT count(*) FROM public_source_runs WHERE state='running'").fetchone()[0]>=2:raise ApiError(503,'采集队列已满，请稍后重试')
                rid=secrets.token_hex(16)
                db.execute('INSERT INTO public_source_runs VALUES(?,?,?,?,?,?,?,?)',(rid,uid,body['operation_id'],body['source_id'],'running',None,None,datetime.now(timezone.utc).isoformat()))
                row=db.execute('SELECT * FROM public_source_runs WHERE id=?',(rid,)).fetchone()
            db.commit()
            if row['state']=='running':launch(database,uid,row)
            handler.reply(202,view(row));return True
    if path.startswith(root+'/runs/') and path.endswith('/import') and handler.command=='POST':
        rid=path.split('/')[-2]
        try:contract.check('import',body)
        except (ValueError,ValidationError):raise ApiError(400,'请选择有效活动并刷新任务版本') from None
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM public_source_runs WHERE id=? AND user_id=?',(rid,uid)).fetchone()
        if not row:raise ApiError(404,'采集记录不存在')
        if row['state']!='succeeded':raise ApiError(409,'请等待采集完成')
        events={e['id']:e for e in json.loads(row['result'])['events']}
        if any(i not in events for i in body['event_ids']):raise ApiError(400,'活动不属于该采集记录')
        saved=db.execute('SELECT value,version FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
        tasks=json.loads(saved['value']) if saved else [];version=saved['version'] if saved else 0
        if version!=body['tasks_version']:raise ApiError(409,'任务已变化，请刷新后重试')
        added=[]
        for eid in body['event_ids']:
            if db.execute('SELECT 1 FROM public_source_imports WHERE user_id=? AND event_id=?',(uid,eid)).fetchone():continue
            event=events[eid];tid=secrets.token_hex(16)
            description='\n'.join(x for x in [event['description'],('活动时间：'+event['start_time']) if event['start_time'] else None,('地点：'+event['location']) if event['location'] else None] if x)
            tasks.append(dict(id=tid,title=event['title'],description=description,content_type='notice',planningExcluded=True,
                due=None,status='not_started',source=contract.SOURCES[row['source']],sourceUrl=event['url'],course=None,
                subtasks=[],plans=[],createdAt=datetime.now(timezone.utc).isoformat(),publicEvent=event))
            db.execute('INSERT INTO public_source_imports VALUES(?,?,?)',(uid,eid,tid));added.append(tid)
        if added:db.execute('INSERT INTO workspace VALUES(?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value,version=excluded.version',(uid,'tasks',json.dumps(tasks),version+1))
        handler.reply(200,dict(added=added,already_imported=len(body['event_ids'])-len(added)));return True
    raise ApiError(404,'公开网站接口不存在')
