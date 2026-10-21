"""Bounded, content-free operational traces in a separate SQLite sidecar."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.error import HTTPError,URLError

TZ = timezone(timedelta(hours=8))
RETENTION_DAYS = 14
MAX_EVENTS = 50000
_context = ContextVar('niuma_operations', default=None)
_lock = threading.Lock()
_dropped = 0
STAGES = {'http','sync_queue','model_queue','collection','attachment_download','attachment_read',
          'model_call','recognition','validation','save','database_lock','commit','outcome','run'}
CODES = {'ok','http_400','unauthorized','forbidden','not_found','conflict','too_large','rate_limited',
         'queue_unavailable','model_configuration','agent_timeout','agent_unavailable','invalid_agent_output',
         'collection_failed','source_changed','source_incomplete','save_failed','database_busy',
         'cache_hit','cache_miss','retry','no_tasks','filtered','succeeded','partial_success','failed',
         'stopped','interrupted','upstream_error','internal_error','cancelled','output_truncated','queued','running'}


def identifier(value):
    value=str(value or '')
    return value if len(value)==32 and all(c in '0123456789abcdef' for c in value) else hashlib.sha256(value.encode()).hexdigest()[:32] if value else ''


def database(path):
    return Path(str(path)+'.operations.sqlite3')


@contextmanager
def connection(path):
    db=sqlite3.connect(database(path),timeout=.2)
    db.row_factory=sqlite3.Row
    try:
        with db:yield db
    finally:db.close()


def initialize(path):
    with connection(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at REAL NOT NULL,
            request_id TEXT NOT NULL,run_id TEXT NOT NULL,stage TEXT NOT NULL,operation TEXT NOT NULL,
            result TEXT NOT NULL,code TEXT NOT NULL,duration_ms REAL NOT NULL,quantity INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS events_at ON events(at);
        CREATE TABLE IF NOT EXISTS resources(at REAL PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS alerts(code TEXT PRIMARY KEY,state TEXT NOT NULL,first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,last_notified REAL,active_since REAL,delivery_error INTEGER NOT NULL DEFAULT 0);
        ''')
        prune(db)


def prune(db,now=None):
    cutoff=(now or time.time())-RETENTION_DAYS*86400
    db.execute('DELETE FROM events WHERE at<?',(cutoff,))
    db.execute('DELETE FROM events WHERE id <= COALESCE((SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET ?),0)',(MAX_EVENTS,))
    db.execute('DELETE FROM resources WHERE at<?',(cutoff,))


@contextmanager
def scope(path,request_id=None,run_id=None,operation=None):
    previous=_context.get() or {}
    token=_context.set(dict(path=str(path),request_id=identifier(request_id or previous.get('request_id') or secrets.token_hex(16)),
        run_id=identifier(run_id if run_id is not None else previous.get('run_id')),operation=operation or previous.get('operation','background')))
    try:yield _context.get()
    finally:_context.reset(token)


def captured():
    return dict(_context.get()) if _context.get() else None


def classify(error):
    cause=error
    for _ in range(5):
        if isinstance(cause,HTTPError) and cause.code==429:return 'rate_limited'
        if isinstance(cause,TimeoutError) or isinstance(cause,URLError) and isinstance(cause.reason,TimeoutError):return 'agent_timeout'
        cause=getattr(cause,'__context__',None)
        if cause is None:break
    code=getattr(error,'code',None)
    if isinstance(code,str) and code in CODES:return code
    if isinstance(error,(TimeoutError,)):return 'agent_timeout'
    if isinstance(error,sqlite3.OperationalError):return 'database_busy' if 'locked' in str(error).lower() else 'save_failed'
    status=getattr(error,'status',getattr(error,'code',None))
    return {400:'http_400',401:'unauthorized',403:'forbidden',404:'not_found',409:'conflict',413:'too_large',429:'rate_limited',502:'upstream_error',503:'queue_unavailable'}.get(status,'internal_error')


def emit(stage,duration_ms=0,*,operation=None,result='ok',code='ok',quantity=1,at=None):
    global _dropped
    context=_context.get()
    if not context:return
    if stage not in STAGES or code not in CODES:return
    operation=operation or context['operation']
    if not isinstance(operation,str) or len(operation)>48 or not all(c.isascii() and (c.isalnum() or c in '._') for c in operation):operation='other'
    try:
        with connection(context['path']) as db:
            db.execute('INSERT INTO events(at,request_id,run_id,stage,operation,result,code,duration_ms,quantity) VALUES(?,?,?,?,?,?,?,?,?)',
                (at or time.time(),context['request_id'],context['run_id'],stage,operation,
                 result if result in ('ok','failed','cancelled') else 'failed',code,
                 max(0,min(float(duration_ms),86400000)) if math.isfinite(float(duration_ms)) else 0,max(0,min(int(quantity),1000000))))
            prune(db)
    except (OSError,sqlite3.Error,ValueError,TypeError):
        with _lock:_dropped+=1


@contextmanager
def span(stage,operation=None):
    start=time.monotonic()
    outcome=dict(result='ok',code='ok')
    try:yield outcome
    except Exception as error:
        code=classify(error)
        if stage=='collection' and code in ('upstream_error','queue_unavailable','internal_error'):code='collection_failed'
        emit(stage,(time.monotonic()-start)*1000,operation=operation,result='failed',code=code)
        raise
    else:emit(stage,(time.monotonic()-start)*1000,operation=operation,**outcome)


class TimedConnection(sqlite3.Connection):
    def __exit__(self,kind,value,traceback):
        if kind is None and self.in_transaction:
            with span('commit'):return super().__exit__(kind,value,traceback)
        return super().__exit__(kind,value,traceback)

    def execute(self,sql,parameters=()):
        if sql.strip().upper()!='BEGIN IMMEDIATE':return super().execute(sql,parameters)
        with span('database_lock'):return super().execute(sql,parameters)

    def commit(self):
        with span('commit'):return super().commit()


class ObservedCompletion:
    """Picklable callable; ChatClient stays its exact trusted class in the runner."""
    def __init__(self,key,context):self.key,self.context=key,context

    def __call__(self,*args,**kwargs):
        from ddl_text.chat_client import ChatClient
        with scope(**self.context),span('model_call'):
            return ChatClient(self.key).complete_json(*args,**kwargs)


def observe_chat(client):
    context=captured()
    if context:
        from ddl_text.chat_client import ChatClient
        if type(client) is ChatClient:client.complete_json=ObservedCompletion(client._key,context)
    return client


def operation(path,method):
    path=path.split('?',1)[0]
    if path in ('/api/health','/api/version') or path.startswith('/api/admin/'):return None
    if path.startswith('/api/auth/'):
        suffix=path.rsplit('/',1)[-1]
        return 'auth.'+suffix.replace('-','_') if suffix in ('login','register','logout','me','email-code','verify-email','reset-password','password','profile') else 'auth.other'
    for prefix,name in (('/api/sources/sync-runs','sync'),('/api/sources/sync','sync'),('/api/recognition/','recognition'),
                        ('/api/planning/','planning'),('/api/decomposition/','decomposition'),('/api/materials/','materials'),
                        ('/api/workspace','workspace'),('/api/data/','workspace'),('/api/sources/','sources')):
        if path.startswith(prefix):return name+'.'+method.lower()
    return None


def aggregate(rows,key):
    groups={}
    for row in rows:groups.setdefault(row[key],[]).append(row)
    result=[]
    for name,items in sorted(groups.items()):
        durations=sorted(row['duration_ms'] for row in items)
        codes={}
        for row in items:
            if row['result']!='ok':codes[row['code']]=codes.get(row['code'],0)+1
        n=len(items)
        result.append({key:name,'count':n,'success':sum(r['result']=='ok' for r in items),
            'failures':sum(r['result']=='failed' for r in items),'p50Ms':round((durations[(n-1)//2]+durations[n//2])/2,2),
            'p95Ms':round(durations[math.ceil(n*.95)-1],2),'errors':[dict(code=k,count=v) for k,v in sorted(codes.items())]})
    return result


def summary(path,business_db,start,end):
    import runtime_environment
    from server import model_queue
    now=time.time()
    with connection(path) as db:
        prune(db,now)
        rows=[dict(r) for r in db.execute('SELECT * FROM events WHERE at>=? AND at<? ORDER BY id',(start,end))]
        trace=rows[-100:][::-1]
        resource=db.execute('SELECT at,value FROM resources ORDER BY at DESC LIMIT 1').fetchone()
        material_times={row['run_id']:row['at'] for row in db.execute("SELECT run_id,MAX(at) AS at FROM events WHERE stage='run' AND operation='materials' AND code='queued' GROUP BY run_id")}
        alerts=[dict(code=r['code'],state=r['state'],firstSeen=r['first_seen'],lastSeen=r['last_seen'],lastNotified=r['last_notified'],deliveryError=bool(r['delivery_error'])) for r in db.execute('SELECT * FROM alerts ORDER BY last_seen DESC')]
    runs={key:0 for key in ('succeeded','partial_success','failed','stopped','interrupted','no_tasks','filtered')}
    queued=running=unknown_wait=0;oldest=0
    records=[dict(row) for row in business_db.execute("SELECT state,json_extract(snapshot,'$.created_at') AS created_at,COALESCE(json_extract(snapshot,'$.updated_at'),json_extract(snapshot,'$.created_at')) AS queued_at FROM source_sync_runs")]
    tables={row[0] for row in business_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'reprocessing_runs' in tables:
        records.extend(dict(row) for row in business_db.execute("SELECT json_extract(snapshot,'$.state') AS state,json_extract(snapshot,'$.created_at') AS created_at,COALESCE(json_extract(snapshot,'$.updated_at'),json_extract(snapshot,'$.created_at')) AS queued_at FROM reprocessing_runs"))
    if 'public_source_runs' in tables:
        records.extend(dict(row) for row in business_db.execute('SELECT state,created_at,created_at AS queued_at FROM public_source_runs'))
    if {'manual_material_jobs','wechat_jobs'}.issubset(tables):
        for row in business_db.execute('SELECT j.id,j.status FROM wechat_jobs j JOIN manual_material_jobs m ON m.job_id=j.id'):
            at=material_times.get(identifier(row['id']))
            records.append(dict(state={'partial_failed':'partial_success','cancelled':'stopped'}.get(row['status'],row['status']),created_at=at,queued_at=at))
    for row in records:
        state=row['state']
        def stamp(value):return float(value) if isinstance(value,(int,float)) else datetime.fromisoformat(value).timestamp()
        try:created=stamp(row['created_at'])
        except (ValueError,KeyError,TypeError):created=None
        if state=='queued':
            queued+=1
            try:oldest=max(oldest,now-stamp(row['queued_at']))
            except (ValueError,TypeError):unknown_wait+=1
        if state in ('crawling','recognizing','running','stopping'):running+=1
        if created is not None and start<=created<end and state in runs:runs[state]+=1
    outcomes={}
    for row in rows:
        if row['stage']=='outcome':outcomes[row['code']]=outcomes.get(row['code'],0)+row['quantity']
    for key in ('no_tasks','filtered'):runs[key]=outcomes.get(key,0)
    model=model_queue.ACTIVE.snapshot()
    resources=dict(json.loads(resource['value']),observedAt=resource['at']) if resource else None
    if resources and now-resource['at']>180:resources['available']=False
    return dict(environment=runtime_environment.environment(),revision=runtime_environment.revision(),generatedAt=datetime.now(TZ).isoformat(),
        start=datetime.fromtimestamp(start,TZ).isoformat(),end=datetime.fromtimestamp(end,TZ).isoformat(),retentionDays=RETENTION_DAYS,
        requests=aggregate([r for r in rows if r['stage']=='http'],'operation'),stages=aggregate([r for r in rows if r['stage'] not in ('http','outcome','run')],'stage'),
        queue=dict(queued=queued,running=running,oldestWaitSeconds=None if unknown_wait else round(max(0,oldest),1),modelQueued=model['queued'],modelRunning=model['running'],modelOldestWaitSeconds=model['oldestWaitSeconds']),
        runs=runs,outcomes=[dict(code=k,count=v) for k,v in sorted(outcomes.items())],resources=resources,
        alerts=alerts,trace=[dict(at=r['at'],requestId=r['request_id'],runId=r['run_id'],stage=r['stage'],operation=r['operation'],result=r['result'],code=r['code'],durationMs=r['duration_ms']) for r in trace],droppedEvents=_dropped,
        methodology='阶段仅记录启用后的观测，轮次状态来自已有实际记录；阶段可嵌套，不能相加作总耗时；数据库锁耗时为显式BEGIN IMMEDIATE获取写锁时间。模型调用不含排队；供应商未透出用量时不估算费用。资源为最新主机探针采样，超过180秒视为过期；详细事件保留14天且最多50000条，达到上限会裁剪最旧记录。')
