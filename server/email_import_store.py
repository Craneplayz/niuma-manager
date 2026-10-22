"""Email import schema, transactions and durable job storage.

No HTTP handlers, mailbox access or model execution belongs here. Ownership
checks use the email scheduler lease; the caller controls transaction commit.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path
from contextlib import contextmanager, nullcontext

import email_db_gate
import email_scheduler

_recovered_paths = set()
_recovery_watchers = set()
_recovery_watchers_lock = threading.Lock()

def database_path(db):
    try:
        row = db.execute('PRAGMA database_list').fetchone()
        return row['file'] if isinstance(row, sqlite3.Row) else row[2]
    except (sqlite3.Error, TypeError):
        return None


def _recover_active_jobs(db, db_path):
    for row in db.execute("SELECT user_id,job_id,snapshot FROM email_import_jobs WHERE state IN ('queued','running')").fetchall():
        job = load_job(db, row['user_id'], row['job_id'], row['snapshot'])
        for item in job['items']:
            if item['state'] in ('queued','processing'):
                item['state'] = 'interrupted'
        job['state'] = 'interrupted'
        job['error'] = '服务已重启，未完成邮件可重试'
        save(db, row['user_id'], row['job_id'], job)
    _recovered_paths.add(db_path)


def _watch_process_owner(db_path):
    was_owner = email_scheduler.owns_process(db_path)
    try:
        while Path(db_path).exists():
            if not email_scheduler.owns_process(db_path):
                if was_owner:
                    _recovered_paths.discard(db_path)
                was_owner = False
                email_scheduler.claim_process_owner(db_path)
            if email_scheduler.owns_process(db_path):
                if not was_owner or db_path not in _recovered_paths:
                    db = None
                    try:
                        db = sqlite3.connect(db_path, timeout=10)
                        db.row_factory = sqlite3.Row
                        with db:
                            _recover_active_jobs(db, db_path)
                        was_owner = True
                    except (sqlite3.Error, ValueError, TypeError, KeyError):
                        was_owner = False
                    finally:
                        if db is not None:
                            db.close()
            time.sleep(5)
    finally:
        with _recovery_watchers_lock:
            _recovery_watchers.discard(db_path)


def _ensure_owner_watcher(db_path):
    with _recovery_watchers_lock:
        if db_path in _recovery_watchers:
            return
        _recovery_watchers.add(db_path)
    thread = threading.Thread(target=_watch_process_owner, args=(db_path,), daemon=True,
                              name='email-scheduler-recovery')
    thread.start()


def initialize(db, recover=True):
    db_path = database_path(db)
    if db_path:
        db_path = str(Path(db_path).resolve())
    owner = email_scheduler.register_process_owner(db, db_path) if db_path else True
    db.execute('''CREATE TABLE IF NOT EXISTS email_import_preferences(
        user_id TEXT PRIMARY KEY, mailbox TEXT, consent_version TEXT, consented_at TEXT,
        process_all INTEGER NOT NULL DEFAULT 0)''')
    columns={r['name'] for r in db.execute('PRAGMA table_info(email_import_preferences)')}
    if 'mailbox' not in columns: db.execute('ALTER TABLE email_import_preferences ADD COLUMN mailbox TEXT')
    db.execute('''CREATE TABLE IF NOT EXISTS email_import_exclusions(
        user_id TEXT NOT NULL, input_id TEXT NOT NULL, excluded INTEGER NOT NULL,
        PRIMARY KEY(user_id,input_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS email_import_jobs(
        user_id TEXT NOT NULL, job_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        state TEXT NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(user_id,job_id),
        UNIQUE(user_id,operation_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS email_import_job_items(
        user_id TEXT NOT NULL, job_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
        input_id TEXT NOT NULL, item TEXT NOT NULL,
        PRIMARY KEY(user_id,job_id,input_id), UNIQUE(user_id,job_id,ordinal))''')
    db.execute('''CREATE TABLE IF NOT EXISTS email_import_user_revision(
        user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS email_import_body_snapshots(
        user_id TEXT NOT NULL,input_id TEXT NOT NULL,record TEXT NOT NULL,
        PRIMARY KEY(user_id,input_id))''')
    db.execute('''CREATE INDEX IF NOT EXISTS email_import_item_state
        ON email_import_job_items(user_id,job_id,json_extract(item,'$.state'),ordinal)''')
    # One-time migration from the original monolithic JSON snapshot.
    for row in db.execute('SELECT user_id,job_id,snapshot FROM email_import_jobs').fetchall():
        legacy = json.loads(row['snapshot'])
        if 'items' in legacy:
            persist_items(db, row['user_id'], row['job_id'], legacy.pop('items'))
            db.execute('UPDATE email_import_jobs SET snapshot=? WHERE user_id=? AND job_id=?',
                (json.dumps(legacy, ensure_ascii=False), row['user_id'], row['job_id']))
    db.execute('''CREATE TABLE IF NOT EXISTS email_recognition_cache(
        user_id TEXT NOT NULL,input_id TEXT NOT NULL,request_id TEXT NOT NULL,
        saved_policy_version TEXT,PRIMARY KEY(user_id,input_id))''')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS email_import_one_active
        ON email_import_jobs(user_id) WHERE state IN ('queued','running')''')
    if recover and owner and db_path not in _recovered_paths:
        _recover_active_jobs(db, db_path)
    if recover and db_path:
        _ensure_owner_watcher(db_path)


def item_counts(item):
    """Count one item identically during full saves and incremental updates."""
    state = item.get('state')
    return dict(processed=int(state in ('has_tasks', 'no_tasks')),
                cached=int(bool(item.get('cached'))),
                excluded=int(state == 'excluded'), failed=int(state == 'failed'),
                pending=int(state in ('queued', 'processing', 'interrupted')))


def counts(job):
    counts = dict(selected=len(job['items']), processed=0, cached=0,
                  excluded=0, failed=0, pending=0)
    for item in job['items']:
        for key, value in item_counts(item).items():
            counts[key] += value
    return counts


def save(db, uid, job_id, job):
    job['counts'] = counts(job)
    persist_items(db, uid, job_id, job.get('items', []))
    stored = {key: value for key, value in job.items() if key != 'items'}
    stored['revision'] = int(stored.get('revision', 0)) + 1
    job['revision'] = stored['revision']
    db.execute('UPDATE email_import_jobs SET state=?,snapshot=? WHERE user_id=? AND job_id=?',
               (job['state'], json.dumps(stored, ensure_ascii=False), uid, job_id))
    bump_list_revision(db, uid)


def bump_list_revision(db, uid):
    db.execute('INSERT INTO email_import_user_revision(user_id,revision) VALUES(?,1) '
               'ON CONFLICT(user_id) DO UPDATE SET revision=revision+1', (uid,))


def persist_items(db, uid, job_id, items):
    existing = {row['input_id']: (row['ordinal'], row['item']) for row in db.execute(
        'SELECT input_id,ordinal,item FROM email_import_job_items WHERE user_id=? AND job_id=?',
        (uid, job_id))}
    keep = set()
    for ordinal, item in enumerate(items):
        input_id = item['input_id']; keep.add(input_id)
        raw = json.dumps(item, ensure_ascii=False, separators=(',', ':'))
        prior = existing.get(input_id)
        if prior != (ordinal, raw):
            db.execute('''INSERT INTO email_import_job_items VALUES(?,?,?,?,?)
                ON CONFLICT(user_id,job_id,input_id) DO UPDATE SET ordinal=excluded.ordinal,item=excluded.item''',
                (uid, job_id, ordinal, input_id, raw))
    for input_id in existing.keys() - keep:
        db.execute('DELETE FROM email_import_job_items WHERE user_id=? AND job_id=? AND input_id=?',
                    (uid, job_id, input_id))


def load_job(db, uid, job_id, raw_snapshot):
    job = json.loads(raw_snapshot)
    if 'items' not in job:
        job['items'] = [json.loads(row['item']) for row in db.execute(
            'SELECT item FROM email_import_job_items WHERE user_id=? AND job_id=? ORDER BY ordinal',
            (uid, job_id))]
    return job


def insert_job(db, uid, job_id, operation_id, state, job):
    persist_items(db, uid, job_id, job.get('items', []))
    stored = {key: value for key, value in job.items() if key != 'items'}
    stored.setdefault('revision', 0)
    db.execute('INSERT INTO email_import_jobs VALUES(?,?,?,?,?)',
               (uid, job_id, operation_id, state, json.dumps(stored, ensure_ascii=False)))
    bump_list_revision(db, uid)


@contextmanager
def connection(path, write=False):
    with email_db_gate.write(path) if write else nullcontext():
        db = sqlite3.connect(path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                if write:
                    db.execute('BEGIN IMMEDIATE')
                yield db
        finally:
            db.close()


def job_states(db,uid,input_ids):
    result={}
    for offset in range(0,len(input_ids),100):
        chunk=input_ids[offset:offset+100]
        if not chunk: continue
        marks=','.join('?' for _ in chunk)
        try:
            rows=db.execute(f'''SELECT e.input_id AS input_id,
                json_extract(e.item,'$.state') AS state,json_extract(e.item,'$.task_count') AS task_count,
                json_extract(e.item,'$.request_id') AS request_id,json_extract(e.item,'$.error') AS error
                FROM email_import_jobs j JOIN email_import_job_items e
                  ON e.user_id=j.user_id AND e.job_id=j.job_id
                WHERE j.user_id=? AND e.input_id IN ({marks})
                ORDER BY j.rowid DESC''',(uid,*chunk)).fetchall()
            for row in rows:
                if row['input_id'] not in result: result[row['input_id']]=dict(row)
        except sqlite3.Error: pass
    return result


def pending_review_counts(db,uid,input_ids,request_ids=None):
    result={}
    for offset in range(0,len(input_ids),100):
        chunk=input_ids[offset:offset+100]
        marks=','.join('?' for _ in chunk)
        rows=db.execute(f'''SELECT json_extract(candidate,'$.input_id') AS input_id,COUNT(*) AS n
            FROM recognition_candidates WHERE user_id=? AND state='pending'
            AND json_extract(candidate,'$.input_id') IN ({marks})
            GROUP BY json_extract(candidate,'$.input_id')''',(uid,*chunk)).fetchall()
        result.update({row['input_id']:row['n'] for row in rows})
    return result


def begin(db):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
