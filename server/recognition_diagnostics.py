"""Bounded content-free diagnostics; detailed views are development/test only."""
from contextlib import contextmanager, closing
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import re
import secrets
import sqlite3
import time
import traceback
import logging
from pathlib import Path

from ddl_text.adapters import safe_schema_detail
from server.runtime_environment import environment, revision
from ddl_text import diagnostics as trace
from contracts.recognition_errors import describe, DEFAULTS
from contracts.check_v031_examples import json_bytes, MAX_RESULT_BYTES

_active = ContextVar('recognition_diagnostic_attempt', default=None)


def detailed():
    return environment() in ('development', 'test')


def current_id():
    attempt = _active.get()
    return attempt['attempt_id'] if attempt else None


def stamp(result):
    """Stamp fresh errors only; preserved retry/checkpoint errors keep their IDs."""
    identity = current_id()
    if not identity: return result
    errors = list(result.get('errors', []))
    errors.extend(row['error'] for row in result.get('record_results', []) if row.get('error'))
    for error in errors: error.setdefault('diagnostic_id', identity)
    return result


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
    safe = trace.sanitize(stage, fields)
    if safe:
        safe['sequence'] = attempt.get('event_count', 0)
        attempt['event_count'] = safe['sequence'] + 1
        attempt['events'].append(safe)


@contextmanager
def capture(db, uid, request_id, *, skip_exceptions=()):
    """Yield an attempt that callers can persist again after a business rollback."""
    started = time.monotonic()
    attempt = dict(attempt_id=secrets.token_hex(12),request_id=request_id,revision=revision(),
                   environment=environment(),started_at=datetime.now(timezone.utc).isoformat(),events=[])
    token = _active.set(attempt)
    raised = None
    skipped = False
    try:
        with trace.scope(event): yield attempt
    except skip_exceptions:
        # Operational cancellation/defer is not a recognition attempt failure.
        skipped = True
        raise
    except Exception as error:
        raised = error
        error._recognition_attempt = attempt
        code = getattr(error,'code', None)
        status = getattr(error,'status',None)
        reason = getattr(error,'diagnostic',None) or DEFAULTS.get(code) or (
            'save_failed' if isinstance(error,sqlite3.Error) else
            'source_changed' if status==409 else
            'invalid_input' if status==400 else
            'model_unavailable' if status in (502,503) else
            'model_timeout' if status in (408,504) else
            'model_rate_limited' if status==429 else 'internal_error')
        event('exception',code=code or 'agent_unavailable',exception_type=type(error).__name__,
              diagnostic=reason,schema=getattr(error,'detail',None), locations=trace.locations(error))
        retryable = getattr(error,'retryable',reason in (
            'internal_error','save_failed','model_unavailable','model_timeout','model_rate_limited','model_busy'))
        error._recognition_failure = describe(dict(code=code or 'agent_unavailable', retryable=retryable is True,
            diagnostic_id=attempt['attempt_id']), reason=reason, detail=getattr(error,'detail',None))
        raise
    finally:
        attempt['seconds'] = round(time.monotonic()-started,3)
        while len(json.dumps(attempt,ensure_ascii=False,indent=2))>60000 and len(attempt['events'])>2:
            del attempt['events'][1]
            attempt['omitted_events'] = attempt.get('omitted_events',0)+1
        _active.reset(token)
        saved = False if skipped else persist(db, uid, attempt)
        if raised is not None and detailed():
            raised._recognition_failure['diagnostics'] = json.dumps(dict(attempt,
                persistence='saved' if saved else 'unavailable'), ensure_ascii=False, indent=2)


def persist(db, uid, attempt):
    """Idempotently save after releasing any transaction that failed with the attempt.

    A capture cannot commit or roll back its caller's business transaction. If
    that transaction is rolled back, the failure handler saves the yielded
    attempt again before starting its error-receipt transaction.
    """
    if attempt is None: return False
    def save(connection):
        initialize(connection)
        connection.execute('INSERT OR IGNORE INTO recognition_attempt_diagnostics VALUES(?,?,?,?,?)',
            (attempt['attempt_id'],uid,attempt['request_id'],attempt['started_at'],json.dumps(attempt)))
        connection.execute('''DELETE FROM recognition_attempt_diagnostics WHERE user_id=? AND id NOT IN (
            SELECT id FROM recognition_attempt_diagnostics WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 200)''',(uid,uid))
    had_transaction = False
    try:
        if db is None: raise sqlite3.OperationalError('diagnostic_connection_unavailable')
        had_transaction = db.in_transaction
        path = db.execute('PRAGMA database_list').fetchone()[2]
        if had_transaction:
            # Statement failures must not leave partial diagnostic writes behind.
            # SQLITE_FULL/IOERR can discard even this savepoint and the outer
            # transaction; detect that separately instead of reporting success.
            point = 'diagnostic_' + secrets.token_hex(8)
            db.execute('SAVEPOINT ' + point)
            try:
                save(db)
            except (sqlite3.Error, OSError):
                if db.in_transaction:
                    db.execute('ROLLBACK TO ' + point)
                    db.execute('RELEASE ' + point)
                raise
            db.execute('RELEASE ' + point)
        elif not path: save(db)
        else:
            with closing(sqlite3.connect(path,timeout=5)) as connection, connection: save(connection)
        return True
    except (sqlite3.Error,OSError) as storage_error:
        try: transaction_lost = had_transaction and not db.in_transaction
        except sqlite3.Error: transaction_lost = had_transaction
        attempt['persistence_error'] = trace.sanitize('diagnostic_persistence', dict(
            code=getattr(storage_error,'sqlite_errorname','storage_error'),
            status='failed', diagnostic='save_failed' if transaction_lost else 'internal_error'))
        # Bounded, sanitized fallback, no exception text or request/user identity.
        logging.getLogger('recognition_diagnostics').error(json.dumps(dict(
            diagnostic_id=attempt['attempt_id'], persistence='unavailable',
            persistence_error=attempt['persistence_error'], events=attempt['events'])))
        if transaction_lost:
            # The business writes no longer exist. This is a save failure, not
            # an optional missing diagnostic. Let the normal failure handler
            # roll back/finalize and return a failed receipt.
            error = sqlite3.OperationalError('recognition_transaction_rolled_back')
            error._recognition_attempt = attempt
            error._recognition_failure = describe(dict(code='agent_unavailable', retryable=True,
                diagnostic_id=attempt['attempt_id']), reason='save_failed')
            if detailed():
                error._recognition_failure['diagnostics'] = json.dumps(dict(attempt,
                    persistence='unavailable', transaction='rolled_back'),ensure_ascii=False,indent=2)
            raise error from None
        return False


def finalize_error(db, uid, error):
    """The request failed: roll back writes before saving its diagnostic independently."""
    value = getattr(error, '_recognition_failure', None)
    attempt = getattr(error, '_recognition_attempt', None)
    if not value: return None
    if attempt is not None:
        if db is not None: db.rollback()
        saved = persist(db, uid, attempt)
        if detailed():
            value = dict(value, diagnostics=json.dumps(dict(attempt,
                persistence='saved' if saved else 'unavailable'),ensure_ascii=False,indent=2))
    return value


def attempts(db, uid, request_id):
    if not detailed(): return []
    return [json.loads(row[0]) for row in db.execute('''SELECT detail FROM recognition_attempt_diagnostics
        WHERE user_id=? AND request_id=? ORDER BY created_at DESC,id DESC LIMIT 20''',(uid,request_id))]


def lookup(db, uid, identity):
    if not detailed(): return []
    return [json.loads(row[0]) for row in db.execute(
        'SELECT detail FROM recognition_attempt_diagnostics WHERE user_id=? AND id=?',(uid,identity))]


def hydrate_errors(db, uid, errors):
    """Inline a bounded preview once per attempt; IDs always allow exact lookup."""
    result, seen, size = [], set(), 0
    for error in errors:
        current = hydrate(db,uid,error)
        detail = current.get('diagnostics')
        if detail:
            identity = current.get('diagnostic_id') or detail
            extra = len(json_bytes(current)) - len(json_bytes({k:v for k,v in current.items() if k != 'diagnostics'}))
            if identity in seen or size + extra > 120000:
                current.pop('diagnostics')
            else:
                seen.add(identity); size += extra
        result.append(current)
    return result


def hydrate_result(db, uid, result):
    """An optional preview must fit inside the *whole* transport result budget."""
    result = dict(result, errors=hydrate_errors(db,uid,result['errors']))
    size = len(json_bytes(result))
    for error in reversed(result['errors']):
        if size <= MAX_RESULT_BYTES: break
        if 'diagnostics' in error:
            before = len(json_bytes(error))
            error.pop('diagnostics')
            size -= before - len(json_bytes(error))
    # Never trim candidates or hide a genuine invalid business result. The
    # transport still validates it; omitted previews remain accessible by ID.
    return result


def bind(db, uid, request_id, error, *, attempt_id=None):
    """Attach only the exact producing attempt's detail; never guess the latest."""
    return hydrate(db, uid, reference(db, uid, request_id, error, attempt_id=attempt_id))


def reference(db, uid, request_id, error, *, attempt_id=None):
    """Bind a durable error to one retained attempt without copying its body."""
    if not error or error.get('diagnostic_id') or not attempt_id: return error
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
    identity = error.get('diagnostic_id')
    if isinstance(identity,str) and re.fullmatch(r'[0-9a-f]{24}',identity):
        if not detailed() or db is None or uid is None: return error
        row = db.execute('SELECT detail FROM recognition_attempt_diagnostics WHERE user_id=? AND id=?',(uid,identity)).fetchone()
        if row: error['diagnostics'] = json.dumps(json.loads(row[0]),ensure_ascii=False,indent=2)
        return error
    if (not detailed() or db is None or uid is None or not isinstance(ref,dict)
            or not isinstance(ref.get('request_id'),str) or not isinstance(ref.get('id'),str)): return error
    row = db.execute('''SELECT detail FROM recognition_attempt_diagnostics
        WHERE user_id=? AND request_id=? AND id=?''',(uid,ref.get('request_id'),ref.get('id'))).fetchone()
    if row:
        error['diagnostic_id'] = ref['id']
        error['diagnostics'] = json.dumps(json.loads(row[0]),ensure_ascii=False,indent=2)
    return error
