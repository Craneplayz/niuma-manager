"""Explicit, content-free problem reports; no automatic exception collection."""
import hashlib
import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = json.loads((Path(__file__).resolve().parent.parent / 'contracts/schemas/error-report-v0.1.schema.json').read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
_LIMIT = 20
_WINDOW = 900
_salt = secrets.token_bytes(32)
_attempts = {}
_lock = threading.Lock()


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS error_reports(
        id TEXT PRIMARY KEY, incident_id TEXT UNIQUE NOT NULL, actor TEXT NOT NULL,
        created_at TEXT NOT NULL, report TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS error_reports_created ON error_reports(created_at);
    ''')


def _admit(identity):
    # A salted, in-memory bucket; addresses and request headers are never stored.
    key = hashlib.sha256(_salt + identity.encode()).hexdigest()
    now = time.monotonic()
    with _lock:
        expired = [key for key, (started, _) in _attempts.items() if now - started >= _WINDOW]
        for old in expired:
            del _attempts[old]
        started, count = _attempts.get(key, (now, 0))
        if count >= _LIMIT or key not in _attempts and len(_attempts) >= 4096:
            return False
        _attempts[key] = (started, count + 1)
        return True


def save(db, body, uid, address, error):
    if not isinstance(body, dict) or not VALIDATOR.is_valid(body):
        raise error(400, '问题报告格式不正确，请刷新页面后重试')
    encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    actor = hashlib.sha256(('error-report:' + uid).encode()).hexdigest() if uid else ''
    # Serialize deduplication with insertion, including concurrent double-clicks.
    db.execute('BEGIN IMMEDIATE')
    old = db.execute('SELECT id,actor,report FROM error_reports WHERE incident_id=?', (body['incident_id'],)).fetchone()
    if old:
        if old['actor'] != actor or old['report'] != encoded:
            raise error(409, '此问题报告已经变化，请刷新页面后重试')
        return dict(contract_version='error-report/0.1', saved=True, report_id=old['id'])
    if not _admit('user:' + uid if uid else 'anonymous:' + address):
        raise error(429, '提交次数较多，请稍后再试')
    now = datetime.now(timezone(timedelta(hours=8)))
    reference = secrets.token_hex(12)
    db.execute('INSERT INTO error_reports VALUES(?,?,?,?,?)',
               (reference, body['incident_id'], actor, now.isoformat(), encoded))
    db.execute('DELETE FROM error_reports WHERE created_at<?', ((now - timedelta(days=90)).isoformat(),))
    return dict(contract_version='error-report/0.1', saved=True, report_id=reference)


def recent(db, start, end):
    return [dict(report_id=row['id'], received_at=row['created_at'], **json.loads(row['report']))
            for row in db.execute('SELECT id,created_at,report FROM error_reports WHERE substr(created_at,1,10) BETWEEN ? AND ? ORDER BY created_at DESC LIMIT 100', (start, end))]
