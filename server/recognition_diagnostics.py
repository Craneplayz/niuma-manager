"""Bounded test-only attempt diagnostics. Never store prompts, bodies or secrets."""
from contextlib import contextmanager, closing
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import re
import secrets
import sqlite3
import time
import traceback
from pathlib import Path

from ddl_text.adapters import safe_schema_detail
from server.runtime_environment import environment, revision

_active = ContextVar('recognition_diagnostic_attempt', default=None)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS recognition_attempt_diagnostics(
        id TEXT PRIMARY KEY,user_id TEXT NOT NULL,request_id TEXT NOT NULL,
        created_at TEXT NOT NULL,detail TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS recognition_diagnostics_owner ON recognition_attempt_diagnostics(user_id,request_id,created_at)')


def event(stage, **fields):
    attempt = _active.get()
    if attempt is None: return
    if len(attempt['events']) >= 200:
        attempt['omitted_events'] = attempt.get('omitted_events', 0) + 1
        # Retain the failure/terminal event even for a long document.
        del attempt['events'][1]
    safe = {'stage': stage}
    for key in ('code','diagnostic','exception_type','mime_type','file_sha256','cache','model','status'):
        value = fields.get(key)
        if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}',value): safe[key] = value
    for key in ('seconds','timeout_seconds','bytes','page_start','page_count','total_pages','completed_pages','native_pages','visual_pages','batch_pages'):
        value = fields.get(key)
        if type(value) in (int,float) and 0 <= value < 1e12: safe[key] = round(value,3)
    detail = safe_schema_detail(fields.get('schema'))
    if detail: safe['schema'] = detail
    attempt['events'].append(safe)


@contextmanager
def capture(db, uid, request_id):
    """Yield an attempt that callers can persist again after a business rollback."""
    if environment() != 'test':
        yield None
        return
    started = time.monotonic()
    attempt = dict(attempt_id=secrets.token_hex(12),request_id=request_id,revision=revision(),
                   started_at=datetime.now(timezone.utc).isoformat(),events=[])
    token = _active.set(attempt)
    try:
        yield attempt
    except Exception as error:
        event('exception',code=getattr(error,'code','recognition_failed'),exception_type=type(error).__name__,
              diagnostic=getattr(error,'diagnostic',None),schema=getattr(error,'detail',None))
        root = Path(__file__).resolve().parents[1]
        attempt['locations'] = [dict(file=str(Path(f.filename).resolve().relative_to(root)).replace('\\','/'),
                                    line=f.lineno,function=f.name)
            for f in traceback.extract_tb(error.__traceback__)[-12:]
            if Path(f.filename).resolve().is_relative_to(root)]
        raise
    finally:
        attempt['seconds'] = round(time.monotonic()-started,3)
        while len(json.dumps(attempt,ensure_ascii=False,indent=2))>60000 and len(attempt['events'])>2:
            del attempt['events'][1]
            attempt['omitted_events'] = attempt.get('omitted_events',0)+1
        _active.reset(token)
        persist(db, uid, attempt)


def persist(db, uid, attempt):
    """Idempotently save after releasing any transaction that failed with the attempt.

    A capture cannot commit or roll back its caller's business transaction. If
    that transaction is rolled back, the failure handler saves the yielded
    attempt again before starting its error-receipt transaction.
    """
    if environment() != 'test' or attempt is None: return
    def save(connection):
        initialize(connection)
        connection.execute('INSERT OR IGNORE INTO recognition_attempt_diagnostics VALUES(?,?,?,?,?)',
            (attempt['attempt_id'],uid,attempt['request_id'],attempt['started_at'],json.dumps(attempt)))
        connection.execute('''DELETE FROM recognition_attempt_diagnostics WHERE user_id=? AND id NOT IN (
            SELECT id FROM recognition_attempt_diagnostics WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 200)''',(uid,uid))
    try:
        path = db.execute('PRAGMA database_list').fetchone()[2]
        if not path or db.in_transaction: save(db)
        else:
            with closing(sqlite3.connect(path,timeout=5)) as connection, connection: save(connection)
    except sqlite3.Error:
        pass  # Diagnostics must not obscure the original failure.


def attempts(db, uid, request_id):
    if environment() != 'test': return []
    return [json.loads(row[0]) for row in db.execute('''SELECT detail FROM recognition_attempt_diagnostics
        WHERE user_id=? AND request_id=? ORDER BY created_at DESC,id DESC LIMIT 20''',(uid,request_id))]


def attach(db, uid, request_id, error):
    if not error or environment() != 'test': return error
    history = attempts(db,uid,request_id)
    if history:
        error = dict(error, diagnostics=json.dumps(history[0],ensure_ascii=False,indent=2))
    return error


def reference(db, uid, request_id, error, *, attempt_id=None):
    """Bind a durable error to one retained attempt without copying its body."""
    if not error or environment() != 'test' or not attempt_id: return error
    row = db.execute('''SELECT id FROM recognition_attempt_diagnostics
        WHERE user_id=? AND request_id=? AND id=?''',(uid,request_id,attempt_id)).fetchone()
    if row:
        error = dict(error, _diagnostic_attempt=dict(id=row[0],request_id=request_id))
    return error


def compact(error):
    """Replace legacy embedded copies with references; never resurrect pruned data."""
    if not error or 'diagnostics' not in error: return error
    error = dict(error)
    detail = error.pop('diagnostics')
    try:
        attempt = json.loads(detail)
        if (isinstance(attempt,dict) and isinstance(attempt.get('attempt_id'),str)
                and re.fullmatch(r'[0-9a-f]{24}',attempt['attempt_id'])
                and isinstance(attempt.get('request_id'),str) and len(attempt['request_id'])<=256):
            error['_diagnostic_attempt'] = dict(id=attempt['attempt_id'],request_id=attempt['request_id'])
    except (TypeError,ValueError):
        pass
    return error


def hydrate(db, uid, error):
    """Present the bound attempt only while it remains in the owner's history."""
    if not error: return error
    error = dict(compact(error))
    ref = error.pop('_diagnostic_attempt',None)
    if (environment() != 'test' or db is None or uid is None or not isinstance(ref,dict)
            or not isinstance(ref.get('request_id'),str) or not isinstance(ref.get('id'),str)): return error
    row = db.execute('''SELECT detail FROM recognition_attempt_diagnostics
        WHERE user_id=? AND request_id=? AND id=?''',(uid,ref.get('request_id'),ref.get('id'))).fetchone()
    if row: error['diagnostics'] = json.dumps(json.loads(row[0]),ensure_ascii=False,indent=2)
    return error
