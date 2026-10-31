"""Content-free email pipeline telemetry in the existing usage database."""
from contextlib import closing, contextmanager
import sqlite3
import time
import math
import secrets
import threading
from collections import deque
from datetime import datetime, timedelta
from ddl_text.model_usage import TOKEN_FIELDS, FINISH_REASONS, MAX_TOKEN_COUNT

try:
    from . import usage
except ImportError:  # direct script-style imports used by the server
    import usage


STAGES = frozenset({
    'headers', 'queue', 'fetch', 'parse', 'recognition', 'job', 'cache_hit',
    'excluded', 'failed', 'model_queue', 'generation', 'validation',
    'dispatch_wait', 'first_result', 'db_wait', 'db_write', 'imap_connect',
    'provider_backoff', 'provider_throttled', 'status',
})
DIAGNOSTIC_STAGES = frozenset({'generation', 'validation'})
DIAGNOSTIC_REASONS = frozenset({'invalid_json', 'duplicate_keys', 'oversized_output',
                                'schema', 'evidence', 'time', 'candidate', 'unknown'})
DIAGNOSTIC_TABLE = 'email_recognition_diagnostics'
EVENT_PREFIX = 'email_'
WRITE_TIMEOUT_SECONDS = 0.25
_capacity_lock = threading.Lock()
_dropped_writes = 0
_status_samples = deque(maxlen=1024)


def _dropped():
    global _dropped_writes
    with _capacity_lock:
        _dropped_writes += 1


def observe_status(duration_ms, response_bytes):
    # In-memory bounded samples avoid adding one SQLite write per status poll.
    if _valid_duration(duration_ms) and type(response_bytes) is int and response_bytes >= 0:
        with _capacity_lock:
            _status_samples.append((duration_ms, response_bytes))


def capacity_snapshot():
    with _capacity_lock:
        samples, dropped = list(_status_samples), _dropped_writes
    durations = sorted(s[0] for s in samples)
    return dict(scope='current_process', droppedWrites=dropped,
                statusSamples=len(samples),
                statusP50Ms=durations[len(durations)//2] if durations else None,
                statusP95Ms=durations[math.ceil(len(durations)*.95)-1] if durations else None,
                statusMeanBytes=sum(s[1] for s in samples)/len(samples) if samples else None)


def _valid_duration(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value)) and value >= 0
    except (OverflowError, TypeError, ValueError):
        return False


def record(path, uid, stage, duration_ms=0, success=True, quantity=1):
    """Best-effort insert into usage_events; never let telemetry break email work.

    Call after the business transaction has ended. `path` is the SQLite database
    path and only the hashed account actor, stage, timing, outcome and quantity
    are stored. Returns False for invalid stages or an unavailable/locked DB.
    """
    if not isinstance(stage, str) or stage not in STAGES:
        return False
    if not _valid_duration(duration_ms):
        return False
    try:
        with closing(sqlite3.connect(path, timeout=WRITE_TIMEOUT_SECONDS)) as db, db:
            db.execute('PRAGMA busy_timeout=250')
            usage.record(db, str(uid), EVENT_PREFIX + stage,
                         success=bool(success) and stage != 'failed',
                         duration_ms=duration_ms, quantity=quantity)
        return True
    except (sqlite3.Error, OSError, TypeError, ValueError):
        _dropped()
        return False


@contextmanager
def measure(path, uid, stage):
    """Record elapsed milliseconds and success when the measured work ends."""
    started = time.perf_counter()
    try:
        yield
    except BaseException:
        record(path, uid, stage, duration_ms=(time.perf_counter() - started) * 1000,
               success=False)
        raise
    else:
        record(path, uid, stage, duration_ms=(time.perf_counter() - started) * 1000,
               success=True)


def initialize(db):
    db.execute(f'''CREATE TABLE IF NOT EXISTS {DIAGNOSTIC_TABLE}(
        id TEXT PRIMARY KEY, actor TEXT NOT NULL, stage TEXT NOT NULL, reason TEXT NOT NULL,
        duration_ms INTEGER NOT NULL, created_at TEXT NOT NULL)''')
    db.execute(f'CREATE INDEX IF NOT EXISTS email_diagnostic_date ON {DIAGNOSTIC_TABLE}(created_at,stage,reason)')

def record_diagnostic(path, uid, stage, reason, duration_ms=0):
    """Store an allowlisted, content-free failure reason for internal diagnostics."""
    if not isinstance(stage, str) or stage not in DIAGNOSTIC_STAGES:
        return False
    if not isinstance(reason, str) or reason not in DIAGNOSTIC_REASONS:
        return False
    if not _valid_duration(duration_ms):
        return False
    now = datetime.now(usage.TZ)
    try:
        with closing(sqlite3.connect(path, timeout=WRITE_TIMEOUT_SECONDS)) as db, db:
            db.execute('PRAGMA busy_timeout=250')
            initialize(db)
            db.execute(f'INSERT INTO {DIAGNOSTIC_TABLE} VALUES(?,?,?,?,?,?)',
                       (secrets.token_hex(16), usage.actor(str(uid)), stage, reason,
                        max(0, round(float(duration_ms))), now.isoformat()))
            db.execute(f'DELETE FROM {DIAGNOSTIC_TABLE} WHERE substr(created_at,1,10)<?',
                       ((now - timedelta(days=90)).date().isoformat(),))
        return True
    except (sqlite3.Error, OSError, TypeError, ValueError):
        _dropped()
        return False

def diagnostics(db, start, end, excluded=()):
    """Read grouped diagnostics without mutating DB; omit excluded hashed actors."""
    try:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (DIAGNOSTIC_TABLE,)).fetchone()
        if not exists:
            return []
        excluded = tuple(excluded)
        where = 'substr(created_at,1,10) BETWEEN ? AND ?'
        args = [start, end]
        if excluded:
            where += ' AND actor NOT IN (' + ','.join('?' for _ in excluded) + ')'
            args.extend(excluded)
        return [dict(row) for row in db.execute(
            f'SELECT stage,reason,count(*) AS count,avg(duration_ms) AS avg_duration_ms '
            f'FROM {DIAGNOSTIC_TABLE} WHERE {where} GROUP BY stage,reason ORDER BY stage,reason', args)]
    except sqlite3.Error:
        return []


TOKEN_TABLE = 'email_model_token_usage'


def _initialize_tokens(db):
    db.execute(f"""CREATE TABLE IF NOT EXISTS {TOKEN_TABLE}(
        id TEXT PRIMARY KEY, actor TEXT NOT NULL, created_at TEXT NOT NULL,
        success INTEGER NOT NULL, finish_reason TEXT,
        prompt_tokens INTEGER, completion_tokens INTEGER,
        total_tokens INTEGER, reasoning_tokens INTEGER)""")
    db.execute(f'CREATE INDEX IF NOT EXISTS email_tokens_date ON {TOKEN_TABLE}(created_at)')


def record_tokens(path, uid, metrics, success=True):
    """One row per observed model attempt; missing provider usage remains NULL.

    Includes rejected/truncated responses. Cache hits do not invoke this path.
    No prompt, response text, email identity, credentials, or arbitrary keys persist.
    """
    if not isinstance(metrics, dict):
        return False
    counts = []
    for field in TOKEN_FIELDS:
        value = metrics.get(field)
        if value is not None and (type(value) is not int or not 0 <= value <= MAX_TOKEN_COUNT):
            return False
        counts.append(value)
    finish = metrics.get('finish_reason')
    if finish is not None and finish not in FINISH_REASONS:
        return False
    now = datetime.now(usage.TZ)
    try:
        with closing(sqlite3.connect(path, timeout=WRITE_TIMEOUT_SECONDS)) as db, db:
            db.execute('PRAGMA busy_timeout=250')
            _initialize_tokens(db)
            db.execute(f'INSERT INTO {TOKEN_TABLE} VALUES(?,?,?,?,?,?,?,?,?)',
                       (secrets.token_hex(16), usage.actor(str(uid)), now.isoformat(),
                        int(bool(success)), finish, *counts))
            db.execute(f'DELETE FROM {TOKEN_TABLE} WHERE substr(created_at,1,10)<?',
                       ((now - timedelta(days=90)).date().isoformat(),))
        return True
    except (sqlite3.Error, OSError, TypeError, ValueError):
        _dropped()
        return False


def token_summary(db, start, end, excluded=()):
    """Admin-only aggregate, with per-counter coverage rather than invented zeroes."""
    empty = dict(attempts=0, failed=0, truncated=0,
                 tokens={k:dict(total=None, reportedAttempts=0) for k in TOKEN_FIELDS})
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TOKEN_TABLE,)).fetchone():
            return empty
        where = 'substr(created_at,1,10) BETWEEN ? AND ?'
        args = [start, end]
        if excluded:
            where += ' AND actor NOT IN (' + ','.join('?' for _ in excluded) + ')'
            args.extend(excluded)
        # SUM() uses SQLite's integer accumulator; token counters are bounded by
        # the provider transport and represent actual model output, not estimates.
        row = db.execute(f"SELECT count(*),sum(success=0),sum(finish_reason='length') FROM {TOKEN_TABLE} WHERE {where}",args).fetchone()
        result = dict(attempts=row[0], failed=row[1] or 0, truncated=row[2] or 0, tokens={})
        for field in TOKEN_FIELDS:
            total, reported = db.execute(f'SELECT sum({field}),count({field}) FROM {TOKEN_TABLE} WHERE {where}',args).fetchone()
            result['tokens'][field] = dict(total=total, reportedAttempts=reported)
        return result
    except sqlite3.Error:
        return empty
