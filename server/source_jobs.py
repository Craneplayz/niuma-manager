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
    if state: run['state'] = state
    run['revision'] += 1
    run['updated_at'] = stamp()
    run['counts'] = {key: sum(r['state'] == key for r in run['records']) for key in contract.ITEM_STATES}
    run['counts'].update(total=len(run['records']), skipped=run['skipped'],
                         added=sum(r['added'] for r in run['records']))
    db.execute('UPDATE source_sync_runs SET state=?,snapshot=? WHERE user_id=? AND run_id=?',
               (run['state'], json.dumps(run), run['user_id'], run['run_id']))


def view(run):
    return {k: v for k, v in run.items() if k != 'user_id'}


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


def start(db, uid, body, ApiError, *, trigger='manual', records=None, parent=None):
    contract.check('retry' if parent else 'start', body)
    prior = db.execute('SELECT snapshot FROM source_sync_runs WHERE user_id=? AND operation_id=?', (uid, body['operation_id'])).fetchone()
    if prior:
        run = json.loads(prior['snapshot'])
        if run['parent_run_id'] != parent: raise ApiError(409, '同一操作编号不能用于不同运行')
        return run
    key = credential(db, uid)
    if not key: raise ApiError(400, '请先保存教学网账号和密码')
    active = db.execute("SELECT snapshot FROM source_sync_runs WHERE user_id=? AND state IN ('queued','crawling','recognizing')", (uid,)).fetchone()
    if active: return json.loads(active['snapshot'])
    with _lock:
        if len(_workers)>=RUN_LIMIT: raise ApiError(503, '同步资源仍在使用或释放中，请稍后重试')
    if db.execute("SELECT count(*) FROM source_sync_runs WHERE state IN ('queued','crawling','recognizing')").fetchone()[0] >= RUN_LIMIT:
        raise ApiError(503, '同步队列已满，请稍后重试')
    run = dict(contract_version=contract.VERSION, run_id=secrets.token_hex(16), user_id=uid,
               operation_id=body['operation_id'], sources=['teaching_web'], trigger=trigger, parent_run_id=parent,
               state='queued', revision=0, created_at=stamp(), updated_at=stamp(), skipped=0,
               records=records or [], counts={}, error=None)
    db.execute('INSERT INTO source_sync_runs VALUES(?,?,?,?,?,?)', (uid, run['run_id'], body['operation_id'], key, 'queued', '{}'))
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
            item.update(state='failed', seconds=round(time.monotonic()-started, 2),
                        error=error('source_changed' if conflict else 'recognition_failed',
                                    exc.message if isinstance(exc, ApiError) else '识别暂时失败，已保存的其他结果不受影响。', not conflict))
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
                bad = any(r['state'] != 'succeeded' for r in run['records'])
                good = any(r['state'] == 'succeeded' for r in run['records'])
                save(db, run, ('partial_success' if good else 'failed') if bad else 'succeeded')
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
        if old['state'] in contract.ACTIVE: raise ApiError(409,'本轮同步仍在执行，请等待或停止后继续')
        key = db.execute('SELECT credential FROM source_sync_runs WHERE user_id=? AND run_id=?',(uid,run_id)).fetchone()[0]
        if credential(db,uid)!=key: raise ApiError(409,'教学网凭据已改变，请重新获取来源')
        records=[]
        for item in old['records']:
            if item['state'] not in ('cancelled','interrupted') and not (item['state']=='failed' and (item['error'] or {}).get('retryable')): continue
            retry = deepcopy(item);retry.update(state='queued',added=0,error=None,seconds=None)
            records.append(retry)
        if not records: raise ApiError(409,'没有可重试的识别记录；来源缺失或采集失败请重新获取来源')
        run=start(db,uid,body,ApiError,trigger='retry',records=records,parent=run_id)
        db.commit()
        if run['state'] in contract.ACTIVE: launch(database,uid,run['run_id'],ApiError)
        handler.reply(202,view(run));return True
    raise ApiError(404,'同步操作不存在')
