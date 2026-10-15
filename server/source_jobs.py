"""Durable teaching sync; crawler/model work never holds a SQLite write lock."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from copy import deepcopy

from contracts import source_sync as contract
from server.model_queue import cancel_if
import recognition
import source_sync
import teaching
import usage
import task_relations

CONCURRENCY = 4
RUN_LIMIT = 4
_slots = threading.BoundedSemaphore(RUN_LIMIT)
_lock = threading.Lock()
_workers = {}


def stamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection(path):
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    try:
        with db: yield db
    finally: db.close()


def initialize(db, recover=True):
    db.execute('''CREATE TABLE IF NOT EXISTS source_sync_runs(
        user_id TEXT NOT NULL, run_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
        credential TEXT NOT NULL, state TEXT NOT NULL, snapshot TEXT NOT NULL,
        UNIQUE(user_id,operation_id))''')
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS source_sync_one_active
        ON source_sync_runs(user_id) WHERE state IN ('queued','crawling','recognizing')""")
    db.execute('''CREATE TABLE IF NOT EXISTS source_sync_operations(
        user_id TEXT NOT NULL, operation_id TEXT NOT NULL, run_id TEXT NOT NULL,
        signature TEXT NOT NULL, PRIMARY KEY(user_id,operation_id))''')
    for row in db.execute('SELECT user_id,operation_id,run_id,snapshot FROM source_sync_runs').fetchall():
        run = json.loads(row['snapshot'])
        db.execute('INSERT OR IGNORE INTO source_sync_operations VALUES(?,?,?,?)',
                   (row['user_id'],row['operation_id'],row['run_id'],
                    operation_signature(run['parent_run_id'],run.get('retry_selection'))))
    if recover:
        for row in db.execute("SELECT * FROM source_sync_runs WHERE state IN ('queued','crawling','recognizing')").fetchall():
            run = json.loads(row['snapshot'])
            for item in run['records']:
                if item['state'] in ('queued', 'running'): item['state'] = 'interrupted'
            run['error'] = error('interrupted', '服务已重启，已保存结果保留；可继续未完成记录。', True)
            save(db, run, 'interrupted')


def credential(db, uid):
    row = db.execute('SELECT username,secret FROM source_credentials WHERE user_id=?', (uid,)).fetchone()
    return hashlib.sha256(row['username'].encode()+b'\0'+row['secret']).hexdigest() if row else None


def error(code, message, retryable=False):
    return dict(code=code, message=message, retryable=retryable)


def save(db, run, state=None):
    previous = db.execute('SELECT snapshot FROM source_sync_runs WHERE run_id=?', (run['run_id'],)).fetchone()
    previous = json.loads(previous['snapshot']) if previous else {}
    if state: run['state'] = state
    run['revision'] += 1
    run['updated_at'] = stamp()
    run['counts'] = {key: sum(r['state'] == key for r in run['records']) for key in contract.ITEM_STATES}
    run['counts'].update(total=len(run['records']), skipped=run['skipped'],
                         added=sum(r['added'] for r in run['records']))
    db.execute('UPDATE source_sync_runs SET state=?,snapshot=? WHERE user_id=? AND run_id=?',
               (run['state'], json.dumps(run), run['user_id'], run['run_id']))
    old_records = {r['request_id']:r for r in previous.get('records',[])}
    for item in run['records']:
        if (item['state'] in ('succeeded','failed','incomplete') and item.get('seconds') is not None
                and old_records.get(item['request_id'],{}).get('state') in ('queued','running')):
            usage.record(db,run['user_id'],'recognition',item['state']!='failed',item['seconds']*1000)
    if previous.get('state') in contract.ACTIVE and run['state'] in contract.FINAL:
        duration=(datetime.fromisoformat(run['updated_at'])-datetime.fromisoformat(run['created_at'])).total_seconds()*1000
        usage.record(db,run['user_id'],'sync',run['state']=='succeeded',duration)


def view(run):
    return {k: v for k, v in run.items() if k not in ('user_id', 'retry_selection')}


def can_retry(item):
    return item['state'] in ('cancelled', 'interrupted') or (
        item['state']=='failed' and (item['error'] or {}).get('retryable', False))


def retry_records(db, uid, old, selection, ApiError):
    """Resolve each requested record against its newest attempt, not a stale parent."""
    originals = {r['request_id']: r for r in old['records']}
    selected = set(selection) if selection is not None else set(originals)
    if not selected.issubset(originals): raise ApiError(400, '所选记录不属于本轮同步')
    latest = {}
    for row in db.execute('SELECT snapshot FROM source_sync_runs WHERE user_id=? ORDER BY rowid DESC', (uid,)):
        run = json.loads(row['snapshot'])
        for item in run['records']:
            if item['request_id'] in selected: latest.setdefault(item['request_id'], (run['run_id'],item))
        if selected.issubset(latest): break
    records = []
    for identity, original in originals.items():
        latest_run, latest_item = latest.get(identity, (old['run_id'],original))
        if (identity not in selected or latest_run!=old['run_id'] or
                not can_retry(original) or not can_retry(latest_item)): continue
        stored = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?', (uid, identity)).fetchone()
        if stored:
            result = json.loads(stored['result'])
            if result['status'] in ('success','no_tasks') or teaching.needs_source_completion(result): continue
        try: teaching.resolve_snapshot(db, uid, identity, ApiError)
        except ApiError as exc:
            if exc.status==409: continue  # removed, replaced or already submitted
            raise
        item = deepcopy(original); item.update(state='queued', added=0, error=None, seconds=None)
        records.append(item)
    if not records: raise ApiError(409, '所选记录已恢复、来源已变化或不可重试；请刷新进度，需要补全内容时重新获取来源')
    return records


def stop(db, run):
    if run['state'] not in contract.ACTIVE: return
    for item in run['records']:
        if item['state']=='queued': item['state']='cancelled'
        elif item['state']=='running': item['state']='interrupted'
    save(db,run,'stopped')


def get(db, uid, run_id, ApiError):
    row = db.execute('SELECT snapshot FROM source_sync_runs WHERE user_id=? AND run_id=?', (uid, run_id)).fetchone()
    if not row: raise ApiError(404, '同步记录不存在')
    return json.loads(row['snapshot'])


def guard(db, uid, run_id, ApiError):
    row = db.execute('SELECT credential,state FROM source_sync_runs WHERE user_id=? AND run_id=?', (uid, run_id)).fetchone()
    if not row or row['state'] not in contract.ACTIVE: raise ApiError(409, '本轮同步已停止或失效')
    if credential(db, uid) != row['credential']: raise ApiError(409, '教学网凭据已变化，请重新同步')


def operation_signature(parent, selection):
    return json.dumps([parent,sorted(selection) if selection is not None else None])


def operation(db, uid, body, parent, ApiError):
    prior = db.execute('''SELECT o.signature,r.snapshot FROM source_sync_operations o
        JOIN source_sync_runs r ON r.user_id=o.user_id AND r.run_id=o.run_id
        WHERE o.user_id=? AND o.operation_id=?''',(uid,body['operation_id'])).fetchone()
    if prior:
        if prior['signature'] != operation_signature(parent,body.get('request_ids')):
            raise ApiError(409, '同一操作编号不能用于不同运行或不同记录')
        return json.loads(prior['snapshot'])


def bind_operation(db, uid, body, parent, run_id):
    db.execute('INSERT INTO source_sync_operations VALUES(?,?,?,?)',
               (uid,body['operation_id'],run_id,operation_signature(parent,body.get('request_ids'))))


def start(db, uid, body, ApiError, *, trigger='manual', records=None, parent=None):
    contract.check('retry' if parent else 'start', body)
    prior = operation(db,uid,body,parent,ApiError)
    if prior: return prior
    key = credential(db, uid)
    if not key: raise ApiError(400, '请先保存教学网账号和密码')
    active = db.execute("SELECT snapshot FROM source_sync_runs WHERE user_id=? AND state IN ('queued','crawling','recognizing')", (uid,)).fetchone()
    if active:
        if parent: raise ApiError(409, '已有同步正在执行，请等待或停止后重试')
        run = json.loads(active['snapshot'])
        bind_operation(db,uid,body,parent,run['run_id'])
        return run
    with _lock:
        if len(_workers)>=RUN_LIMIT: raise ApiError(503, '同步资源仍在使用或释放中，请稍后重试')
    if db.execute("SELECT count(*) FROM source_sync_runs WHERE state IN ('queued','crawling','recognizing')").fetchone()[0] >= RUN_LIMIT:
        raise ApiError(503, '同步队列已满，请稍后重试')
    run = dict(contract_version=contract.VERSION, run_id=secrets.token_hex(16), user_id=uid,
               operation_id=body['operation_id'], sources=['teaching_web'], trigger=trigger, parent_run_id=parent,
               state='queued', revision=0, created_at=stamp(), updated_at=stamp(), skipped=0,
               records=records or [], counts={}, error=None)
    if parent: run['retry_selection'] = sorted(body['request_ids']) if 'request_ids' in body else None
    db.execute('INSERT INTO source_sync_runs VALUES(?,?,?,?,?,?)', (uid, run['run_id'], body['operation_id'], key, 'queued', '{}'))
    bind_operation(db,uid,body,parent,run['run_id'])
    save(db, run)
    return run


def record(request):
    row = request['records'][0]
    return dict(request_id=request['request_id'], label=(row.get('text', '').split('\n')[0] or '课程通知')[:160],
                course=row.get('course_or_project'), state='queued', added=0, error=None, seconds=None)


def cancelled(path, uid, run_id, ApiError):
    with connection(path) as db:
        try: guard(db, uid, run_id, ApiError)
        except ApiError: return True
    return False


def auto_apply(db, uid, request_id, ApiError):
    batch = db.execute('SELECT request,demo FROM recognition_batches WHERE user_id=? AND request_id=?', (uid, request_id)).fetchone()
    request = json.loads(batch['request'])
    added = 0
    for item in db.execute('SELECT * FROM recognition_candidates WHERE user_id=? AND request_id=? AND state="pending" AND version=1', (uid, request_id)).fetchall():
        candidate = json.loads(item['candidate'])
        if not recognition.can_auto_apply(candidate, request, batch['demo']): continue
        # Same submission/ownership/user-decision checks as the interactive endpoint.
        if teaching.submission.review_reason(teaching.submission.for_request(db, uid, request_id)): continue
        recognition.resolve_candidate(db, uid, item['id'], dict(action='create', auto_apply=True, version=1), ApiError)
        added += 1
    return added


def process_record(path, uid, run_id, request_id, ApiError):
    started = time.monotonic()
    try:
        with connection(path) as db:
            db.execute('BEGIN IMMEDIATE'); guard(db, uid, run_id, ApiError)
            run = get(db, uid, run_id, ApiError)
            next(r for r in run['records'] if r['request_id']==request_id)['state'] = 'running'
            save(db, run)
        with cancel_if(lambda: cancelled(path, uid, run_id, ApiError)), connection(path) as db:
            teaching.run(db, uid, dict(contract_version=teaching.RUN, request_id=request_id), True, ApiError,
                         guard=lambda current: guard(current, uid, run_id, ApiError))
            if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
            guard(db, uid, run_id, ApiError)
            batch = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?', (uid, request_id)).fetchone()
            result = json.loads(batch['result'])
            run = get(db, uid, run_id, ApiError)
            item = next(r for r in run['records'] if r['request_id']==request_id)
            if teaching.needs_source_completion(result):
                item.update(state='incomplete', error=error('source_incomplete', '来源尚未完整取得，请重新获取来源或查看原文。'))
            elif result['status'] in ('success', 'no_tasks'):
                item.update(state='succeeded', added=auto_apply(db, uid, request_id, ApiError), error=None)
            else:
                failure = next((r.get('error') for r in result['record_results'] if r.get('error')), {})
                item.update(state='failed', error=error(failure.get('code', 'recognition_failed'), failure.get('message', '识别失败，请重试。'), failure.get('retryable', True)))
            item['seconds'] = round(time.monotonic()-started, 2)
            save(db, run)
    except Exception as exc:
        with connection(path) as db:
            db.execute('BEGIN IMMEDIATE')
            run = get(db, uid, run_id, ApiError)
            if run['state'] not in contract.ACTIVE: return
            item = next(r for r in run['records'] if r['request_id']==request_id)
            conflict = isinstance(exc, ApiError) and exc.status==409
            configuration = getattr(exc, 'code', None)=='model_configuration'
            item.update(state='failed', seconds=round(time.monotonic()-started, 2),
                        error=error('source_changed' if conflict else 'model_configuration' if configuration else 'recognition_failed',
                                    exc.message if isinstance(exc, ApiError) else '识别暂时失败，已保存的其他结果不受影响。', not (conflict or configuration)))
            save(db, run)


def process(path, uid, run_id, ApiError):
    try:
        with _slots:
            with connection(path) as db:
                db.execute('BEGIN IMMEDIATE'); guard(db, uid, run_id, ApiError)
                run = get(db, uid, run_id, ApiError)
                save(db, run, 'recognizing' if run['parent_run_id'] else 'crawling')
            if not run['parent_run_id']:
                with connection(path) as db:
                    response = source_sync.collect(db, uid, ApiError, path.parent/'credential.key',
                                                   guard=lambda current: guard(current, uid, run_id, ApiError))
                    task_relations.refresh_completions(db,uid)
                    if len(response['requests']) > 1000: raise ApiError(413,'单轮待识别通知超过 1000 条，请缩小采集范围')
                    run = get(db, uid, run_id, ApiError)
                    run['records'] = [record(r) for r in response['requests']]
                    run['skipped'] = response['summary']['skipped']
                    for item in run['records']:
                        db.execute('INSERT INTO recognition_import_records VALUES(?,?,?,?,?,0) ON CONFLICT(user_id,request_id) DO UPDATE SET import_id=excluded.import_id,created_at=excluded.created_at,source=excluded.source,archived=0',
                                   (uid, item['request_id'], run_id, run['created_at'], '教学网'))
                    save(db, run, 'recognizing')
            pending = iter(run['records'])
            with ThreadPoolExecutor(max_workers=CONCURRENCY, thread_name_prefix='source-record') as pool:
                futures = set()
                exhausted = False
                while not exhausted or futures:
                    if cancelled(path, uid, run_id, ApiError): break
                    while not exhausted and len(futures) < CONCURRENCY:
                        item = next(pending, None)
                        if item is None: exhausted=True;break
                        futures.add(pool.submit(process_record, path, uid, run_id, item['request_id'], ApiError))
                    if futures:
                        done, futures = wait(futures, timeout=.25, return_when=FIRST_COMPLETED)
                        for future in done: future.result()
            with connection(path) as db:
                db.execute('BEGIN IMMEDIATE')
                run = get(db, uid, run_id, ApiError)
                if run['state'] not in contract.ACTIVE: return
                try: guard(db,uid,run_id,ApiError)
                except ApiError as exc:
                    run['error'] = error('source_changed',exc.message,False)
                    for item in run['records']:
                        if item['state'] in ('queued','running'):
                            item.update(state='failed',error=run['error'])
                bad = any(r['state'] != 'succeeded' for r in run['records'])
                good = any(r['state'] == 'succeeded' for r in run['records'])
                save(db, run, ('partial_success' if good else 'failed') if bad else 'succeeded')
                relation_job=task_relations.start(db,uid,'sync:'+run_id) if run['state'] in ('succeeded','partial_success') else None
            task_relations.launch(path,uid,relation_job,ApiError)
    except Exception as exc:
        with connection(path) as db:
            db.execute('BEGIN IMMEDIATE')
            run = get(db, uid, run_id, ApiError)
            if run['state'] not in contract.ACTIVE: return
            run['error'] = error('collection_failed', exc.message if isinstance(exc, ApiError) else '同步暂时失败，请重新获取来源。', True)
            for item in run['records']:
                if item['state'] in ('queued','running'): item['state']='interrupted'
            save(db, run, 'failed')


def launch(path, uid, run_id, ApiError):
    key = (str(path), run_id)
    def work():
        try: process(path, uid, run_id, ApiError)
        finally:
            with _lock: _workers.pop(key, None)
    with _lock:
        if key in _workers: return
        thread = threading.Thread(target=work, daemon=True, name='source-sync')
        _workers[key] = thread; thread.start()


def wait_for_jobs(path, timeout=30):
    until = time.monotonic()+timeout
    while time.monotonic() < until:
        with _lock: workers = [t for (p,_),t in _workers.items() if p==str(path)]
        if not workers: return
        for worker in workers: worker.join(.05)
    raise TimeoutError('source sync workers still running')


def route(handler, db, uid, path, body, ApiError, database):
    root = '/api/sources/sync-runs'
    if not (path==root or path.startswith(root+'/')): return False
    if path==root and handler.command=='POST':
        db.execute('BEGIN IMMEDIATE')
        run = start(db, uid, body, ApiError)
        db.commit()
        if run['state'] in contract.ACTIVE: launch(database, uid, run['run_id'], ApiError)
        handler.reply(202, view(run)); return True
    if path==root and handler.command=='GET':
        rows = db.execute('SELECT snapshot FROM source_sync_runs WHERE user_id=? ORDER BY rowid DESC LIMIT 20', (uid,)).fetchall()
        handler.reply(200, dict(contract_version=contract.VERSION, runs=[view(json.loads(r['snapshot'])) for r in rows])); return True
    run_id, *suffix = path[len(root)+1:].split('/')
    if handler.command=='GET' and not suffix:
        handler.reply(200, view(get(db, uid, run_id, ApiError))); return True
    if handler.command=='POST' and suffix==['stop']:
        contract.check('stop',body); db.execute('BEGIN IMMEDIATE')
        run = get(db, uid, run_id, ApiError)
        stop(db,run)
        handler.reply(200,view(run)); return True
    if handler.command=='POST' and suffix==['retry']:
        contract.check('retry',body); db.execute('BEGIN IMMEDIATE')
        old = get(db,uid,run_id,ApiError)
        # A lost HTTP response can be retried even after its child has completed.
        prior = operation(db,uid,body,run_id,ApiError)
        if prior:
            run = start(db,uid,body,ApiError,trigger='retry',parent=run_id)
            db.commit()
            if run['state'] in contract.ACTIVE: launch(database,uid,run['run_id'],ApiError)
            handler.reply(202,view(run));return True
        if old['state'] in contract.ACTIVE: raise ApiError(409,'本轮同步仍在执行，请等待或停止后继续')
        key = db.execute('SELECT credential FROM source_sync_runs WHERE user_id=? AND run_id=?',(uid,run_id)).fetchone()[0]
        if credential(db,uid)!=key: raise ApiError(409,'教学网凭据已改变，请重新获取来源')
        records=retry_records(db,uid,old,body.get('request_ids'),ApiError)
        run=start(db,uid,body,ApiError,trigger='retry',records=records,parent=run_id)
        db.commit()
        if run['state'] in contract.ACTIVE: launch(database,uid,run['run_id'],ApiError)
        handler.reply(202,view(run));return True
    raise ApiError(404,'同步操作不存在')
