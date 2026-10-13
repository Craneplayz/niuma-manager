"""Opt-in durable schedules; one API process owns a database and its job workers."""
import json
import threading
import time

from contracts import source_sync as contract
import runtime_environment
import source_jobs as jobs


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS source_sync_schedules(
        user_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, interval_minutes INTEGER NOT NULL,
        credential TEXT NOT NULL, revision INTEGER NOT NULL, next_run_at REAL,
        last_run_id TEXT, error TEXT)''')


def view(db, uid):
    row = db.execute('SELECT * FROM source_sync_schedules WHERE user_id=?', (uid,)).fetchone()
    return dict(contract_version=contract.VERSION, available=runtime_environment.scheduled_sync_available(),
                sources=['teaching_web'], enabled=bool(row and row['enabled']),
                interval_minutes=row['interval_minutes'] if row else 180,
                revision=row['revision'] if row else 0, next_run_at=row['next_run_at'] if row else None,
                last_run_id=row['last_run_id'] if row else None, error=json.loads(row['error']) if row and row['error'] else None)


def pause(db, uid, message):
    db.execute('UPDATE source_sync_schedules SET enabled=0,next_run_at=NULL,revision=revision+1,error=? WHERE user_id=?',
               (json.dumps(jobs.error('credentials_changed', message)), uid))
    stop_scheduled(db, uid)


def stop_scheduled(db, uid):
    for row in db.execute("SELECT snapshot FROM source_sync_runs WHERE user_id=? AND state IN ('queued','crawling','recognizing')", (uid,)).fetchall():
        run = json.loads(row['snapshot'])
        if run['trigger']=='scheduled': jobs.stop(db, run)


def route(handler, db, uid, path, body, ApiError):
    if path!='/api/sources/sync-schedule': return False
    if handler.command=='GET':
        handler.reply(200, view(db, uid)); return True
    if handler.command!='PUT': raise ApiError(405, '操作不支持')
    contract.check('schedule_update', body)
    db.execute('BEGIN IMMEDIATE')
    old = view(db, uid)
    if body['revision']!=old['revision']: raise ApiError(409, '定时设置已变化，请刷新后重新设置')
    key = jobs.credential(db, uid)
    if body['enabled']:
        if not runtime_environment.scheduled_sync_available(): raise ApiError(409, '当前环境尚未开启定时同步，请联系管理员')
        if not body['consent']: raise ApiError(400, '请明确同意定期获取并发送所选来源内容')
        if not key: raise ApiError(400, '请先保存教学网凭据')
    if not body['enabled']: stop_scheduled(db, uid)
    db.execute('''INSERT INTO source_sync_schedules VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled,interval_minutes=excluded.interval_minutes,
        credential=excluded.credential,revision=excluded.revision,next_run_at=excluded.next_run_at,error=NULL''',
        (uid, int(body['enabled']), body['interval_minutes'], key or '', old['revision']+1,
         time.time()+body['interval_minutes']*60 if body['enabled'] else None, old['last_run_id'], None))
    handler.reply(200, view(db, uid)); return True


def tick(path, ApiError, now=None):
    if not runtime_environment.scheduled_sync_available(): return
    now = time.time() if now is None else now
    launches=[]
    with jobs.connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        rows=db.execute('SELECT * FROM source_sync_schedules WHERE enabled=1 ORDER BY next_run_at,user_id').fetchall()
        for row in rows:
            uid=row['user_id']
            if jobs.credential(db, uid)!=row['credential'] or not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
                pause(db,uid,'教学网凭据已变化，定时同步已暂停；请核对账号后重新启用。'); continue
            if row['next_run_at']>now: continue
            active=db.execute("SELECT 1 FROM source_sync_runs WHERE user_id=? AND state IN ('queued','crawling','recognizing')",(uid,)).fetchone()
            if active:
                # An ongoing manual run already refreshes this source; no immediate catch-up.
                db.execute('UPDATE source_sync_schedules SET next_run_at=? WHERE user_id=?',(now+row['interval_minutes']*60,uid)); continue
            body=dict(contract_version=contract.VERSION,operation_id=f"scheduled:{uid}:{row['revision']}:{int(row['next_run_at'])}",sources=['teaching_web'],consent=True)
            try: run=jobs.start(db,uid,body,ApiError,trigger='scheduled')
            except ApiError as exc:
                if exc.status!=503: raise
                # Shared capacity full: bounded backoff without consuming a model request.
                db.execute('UPDATE source_sync_schedules SET next_run_at=? WHERE user_id=?',(now+60,uid)); continue
            db.execute('UPDATE source_sync_schedules SET next_run_at=?,last_run_id=?,error=NULL WHERE user_id=?',
                       (now+row['interval_minutes']*60,run['run_id'],uid))
            launches.append((uid,run['run_id']))
    for uid,run_id in launches: jobs.launch(path,uid,run_id,ApiError)


def start_worker(path, ApiError):
    stop=threading.Event()
    def work():
        while not stop.is_set():
            try: tick(path,ApiError)
            except Exception as exc:
                # Never put account details or provider responses into process logs.
                print('source scheduler tick failed: '+type(exc).__name__,flush=True)
            stop.wait(30)
    worker=threading.Thread(target=work,name='source-scheduler',daemon=True)
    if runtime_environment.scheduled_sync_available(): worker.start()
    return stop
