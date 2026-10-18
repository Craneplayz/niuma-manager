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
    if environment() != 'test':
        yield
        return
    started = time.monotonic()
    attempt = dict(attempt_id=secrets.token_hex(12),request_id=request_id,revision=revision(),
                   started_at=datetime.now(timezone.utc).isoformat(),events=[])
    token = _active.set(attempt)
    try:
        yield
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
        def save(connection):
            initialize(connection)
            connection.execute('INSERT INTO recognition_attempt_diagnostics VALUES(?,?,?,?,?)',
                (attempt['attempt_id'],uid,request_id,attempt['started_at'],json.dumps(attempt)))
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
