"""Durable manual-text recognition; results and terminal state commit together."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from urllib.parse import unquote

import recognition
from server import recognition_diagnostics as diagnostics
from contracts.recognition_errors import describe
from server.model_queue import CONCURRENCY, cancel_if

ACTIVE = ('queued', 'recognizing')
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
    finally:
        db.close()


def initialize(db, recover=True, *, resume_queued=False):
    db.execute('''CREATE TABLE IF NOT EXISTS text_import_jobs(
        user_id TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
        request TEXT NOT NULL, state TEXT NOT NULL, attempt INTEGER NOT NULL,
        error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, instance_id TEXT NOT NULL,
        PRIMARY KEY(user_id,request_id))''')
    if not db.execute("SELECT 1 FROM pragma_table_info('text_import_jobs') WHERE name='instance_id'").fetchone():
        db.execute('ALTER TABLE text_import_jobs ADD COLUMN instance_id TEXT')
    for row in db.execute('SELECT user_id,request_id FROM text_import_jobs WHERE instance_id IS NULL').fetchall():
        db.execute('UPDATE text_import_jobs SET instance_id=? WHERE user_id=? AND request_id=?',
                   (secrets.token_hex(16),row['user_id'],row['request_id']))
    db.execute('DROP INDEX IF EXISTS text_import_one_active')
    db.execute("""CREATE INDEX IF NOT EXISTS text_import_waiting ON text_import_jobs(updated_at)
                  WHERE state='queued'""")
    if recover:
        db.execute("""UPDATE text_import_jobs SET state='interrupted',updated_at=?,error=?
            WHERE state='recognizing' OR (state='queued' AND ?=0)""", (stamp(), json.dumps(dict(
                code='interrupted', message='服务已重启，识别已中断，请重试。', retryable=True)), resume_queued))
        return resume_queued and db.execute("SELECT 1 FROM text_import_jobs WHERE state='queued' LIMIT 1").fetchone() is not None


def view(row):
    return dict(request_id=row['request_id'], state=row['state'], attempt=row['attempt'],
                created_at=row['created_at'], updated_at=row['updated_at'],
                error=json.loads(row['error']) if row['error'] else None)


def listing(db, uid):
    rows = db.execute(
        """SELECT * FROM text_import_jobs WHERE user_id=?
           ORDER BY state IN ('queued','recognizing') DESC,updated_at DESC LIMIT 100""", (uid,))
    return envelope(db, uid, rows)


def envelope(db, uid, rows):
    jobs = [view(row) for row in rows]
    errors = diagnostics.hydrate_errors(db,uid,[describe(job['error']) for job in jobs if job['error']])
    hydrated = iter(errors)
    for job in jobs:
        if job['error']: job['error'] = next(hydrated)
    return dict(contract_version='text-import-jobs/0.1', jobs=jobs)


def find(db, uid, rid, ApiError):
    row = db.execute('SELECT * FROM text_import_jobs WHERE user_id=? AND request_id=?', (uid,rid)).fetchone()
    if row is None: raise ApiError(404, '文本导入记录不存在')
    return row


def create(db, uid, request, ApiError):
    try:
        recognition.validate_request(request)
        if (request['contract_version'] != '0.2' or len(request['request_id']) > 500 or len(request['records']) != 1 or
                request['records'][0]['platform'] != 'manual' or
                not 1 <= len(request['records'][0]['text'].strip()) <= 4000):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ApiError(400, '请提交一段不超过 4000 字的文本通知') from None
    rid = request['request_id']
    fingerprint = hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT * FROM text_import_jobs WHERE user_id=? AND request_id=?', (uid,rid)).fetchone()
    if row:
        if row['fingerprint'] != fingerprint: raise ApiError(409, '同一导入编号不能用于不同内容')
        return row
    previous = db.execute('SELECT fingerprint FROM recognition_batches WHERE user_id=? AND request_id=?', (uid,rid)).fetchone()
    if previous and previous['fingerprint'] != fingerprint: raise ApiError(409, '同一导入编号不能用于不同内容')
    now = stamp()
    db.execute('INSERT INTO text_import_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
               (uid,rid,fingerprint,json.dumps(request),'queued',1,None,now,now,secrets.token_hex(16)))
    return find(db, uid, rid, ApiError)


class Superseded(Exception):
    pass


def process(path, uid, rid, attempt, ApiError, *, instance_id):
    def guard(db):
        row = db.execute('SELECT state,attempt,instance_id FROM text_import_jobs WHERE user_id=? AND request_id=?', (uid,rid)).fetchone()
        if not row or row['instance_id'] != instance_id or row['attempt'] != attempt or row['state'] not in ACTIVE:
            raise Superseded()
        if not db.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone(): raise Superseded()

    def cancelled():
        with connection(path) as db:
            try: guard(db)
            except Superseded: return True
        return False

    def admitted():
        with connection(path) as db:
            db.execute('BEGIN IMMEDIATE')
            guard(db)
            db.execute("UPDATE text_import_jobs SET state='recognizing',updated_at=? WHERE user_id=? AND request_id=?",
                       (stamp(),uid,rid))

    try:
        with connection(path) as db:
            guard(db)
            row = find(db,uid,rid,ApiError)
            request = json.loads(row['request'])
            with cancel_if(cancelled):
                result = recognition.process_batch(db,uid,request,ApiError,retry=True,
                                                   before_save=guard,on_admitted=admitted)
            if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
            guard(db)
            if rid.startswith('quick-add:'):
                recognition.add_quick_results(db,uid,request,ApiError)
            error = result['errors'][0] if result.get('errors') else None
            db.execute('UPDATE text_import_jobs SET state=?,error=?,updated_at=? WHERE user_id=? AND request_id=?',
                       (result['status'],json.dumps(error) if error else None,stamp(),uid,rid))
    except Superseded:
        pass
    except Exception as exc:
        try:
            with connection(path) as db:
                guard(db)
                error = diagnostics.finalize_error(db,uid,exc)
                if error is None:
                    error = describe(dict(code='agent_unavailable',retryable=True),reason='internal_error')
                # Store the safe attempt reference, never the embedded detail.
                error = diagnostics.compact(error)
                db.execute('BEGIN IMMEDIATE')
                guard(db)
                db.execute("UPDATE text_import_jobs SET state='failed',error=?,updated_at=? WHERE user_id=? AND request_id=?",
                           (json.dumps(error),stamp(),uid,rid))
        except Superseded:
            pass


def dispatch(path, ApiError):
    with _lock:
        fill_windows(path, ApiError)


def fill_windows(path, ApiError):
    # Called under _lock. A worker waiting for shared model admission still has
    # state queued, so skip its exact attempt when selecting the next jobs.
    if len(_workers) >= CONCURRENCY:
        return
    with connection(path) as db:
        rows = db.execute("""SELECT user_id, request_id, attempt, instance_id FROM text_import_jobs
                             WHERE state='queued' ORDER BY updated_at, rowid LIMIT ?""",
                          (CONCURRENCY,)).fetchall()
        for row in rows:
            key = (str(path), *row)
            if key in _workers:
                continue
            worker = threading.Thread(target=run_job, args=(path, key, ApiError),
                                      daemon=True, name='text-import')
            _workers[key] = worker
            try:
                worker.start()
            except RuntimeError:
                del _workers[key]
                db.execute("""UPDATE text_import_jobs SET state='failed', error=?, updated_at=?
                              WHERE user_id=? AND request_id=? AND attempt=? AND instance_id=? AND state='queued'""",
                           (json.dumps(dict(code='agent_unavailable', retryable=True,
                                            message='识别暂时无法启动，请稍后重试。')), stamp(), *row))
            if len(_workers) >= CONCURRENCY:
                break


def run_job(path, key, ApiError):
    _, uid, rid, attempt, instance_id = key
    try:
        process(path, uid, rid, attempt, ApiError, instance_id=instance_id)
    finally:
        with _lock:
            del _workers[key]
            fill_windows(path, ApiError)


def wait_for_jobs(path, timeout=30):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        with _lock: workers = [t for key,t in _workers.items() if key[0] == str(path)]
        if not workers: return
        for worker in workers: worker.join(.05)
    raise TimeoutError('text import workers still running')


def route(handler, db, uid, path, body, ApiError, database):
    base = '/api/recognition/text-jobs'
    if path != base and not path.startswith(base+'/'): return False
    launch_row = None
    if path == base and handler.command == 'GET':
        pass
    elif handler.command == 'GET' and path.startswith(base + '/'):
        row = find(db, uid, unquote(path[len(base)+1:]), ApiError)
        handler.reply(200, envelope(db, uid, [row]))
        return True
    elif path == base and handler.command == 'POST':
        launch_row = create(db,uid,body,ApiError)
    elif path.startswith(base+'/') and handler.command == 'POST':
        parts = path[len(base)+1:].split('/')
        if len(parts) != 2 or parts[1] not in ('stop','retry'): raise ApiError(404,'接口不存在')
        rid, action = unquote(parts[0]), parts[1]
        if set(body) != {'attempt'} or type(body['attempt']) is not int or body['attempt'] < 1:
            raise ApiError(400,'请提供当前识别尝试编号')
        db.execute('BEGIN IMMEDIATE')
        row = find(db,uid,rid,ApiError)
        if row['attempt'] == body['attempt']:
            if action == 'stop' and row['state'] in ACTIVE:
                db.execute("UPDATE text_import_jobs SET state='stopped',error=NULL,updated_at=? WHERE user_id=? AND request_id=?", (stamp(),uid,rid))
            elif action == 'retry' and row['state'] in ('failed','partial_success','stopped','interrupted'):
                db.execute("UPDATE text_import_jobs SET state='queued',attempt=attempt+1,error=NULL,updated_at=? WHERE user_id=? AND request_id=?", (stamp(),uid,rid))
                launch_row = find(db,uid,rid,ApiError)
    else:
        raise ApiError(404,'接口不存在')
    # Commit acceptance before starting work or writing to a possibly closed socket.
    db.commit()
    if launch_row is not None and launch_row['state'] in ACTIVE:
        dispatch(database, ApiError)
    handler.reply(200,listing(db,uid))
    return True
