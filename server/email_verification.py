"""Persistent, single-use email challenges; delivery credentials stay outside the DB."""
import hashlib
import hmac
import math
import os
import re
import secrets
import time
from pathlib import Path

import mail_delivery

CODE_TTL = 600
MAX_ATTEMPTS = 5
SEND_LIMITS = (
    ('email', 60, 1), ('email', 3600, 5), ('email', 86400, 10),
    ('ip', 3600, 20), ('ip', 86400, 50),
    ('global', 3600, 100), ('global', 86400, 500),
)
GUESS_LIMITS = (('email', 3600, 20), ('ip', 3600, 100), ('global', 3600, 1000))


class VerificationError(Exception):
    def __init__(self, status, code, message, retry_after=None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retry_after = retry_after


def normalize_email(value):
    if not isinstance(value, str) or len(value) > 254:
        raise VerificationError(400, 'email_invalid', '请输入有效的邮箱地址')
    value = value.strip().lower()
    try:
        local, domain = value.split('@')
        domain = domain.encode('idna').decode('ascii')
        labels = domain.split('.')
        valid = (len(local) <= 64 and bool(re.fullmatch(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]+", local))
                 and not local.startswith('.') and not local.endswith('.') and '..' not in local
                 and len(labels) >= 2 and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in labels))
        if not valid or len(local + '@' + domain) > 254:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise VerificationError(400, 'email_invalid', '请输入有效的邮箱地址') from None
    return local + '@' + domain


def initialize(db):
    # A side table preserves the existing users schema and every legacy account.
    db.executescript('''
        CREATE TABLE IF NOT EXISTS verified_emails(user_id TEXT PRIMARY KEY, verified_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS email_challenges(
            id TEXT PRIMARY KEY, email TEXT NOT NULL, purpose TEXT NOT NULL, user_id TEXT NOT NULL,
            code_hash TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS email_challenge_owner ON email_challenges(email,purpose,user_id);
        CREATE TABLE IF NOT EXISTS email_rate_events(
            kind TEXT NOT NULL, subject TEXT NOT NULL, occurred_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS email_rate_lookup ON email_rate_events(kind,subject,occurred_at);
    ''')


def verified(db, uid):
    return bool(db.execute('SELECT 1 FROM verified_emails WHERE user_id=?', (uid,)).fetchone())


def mark_verified(db, uid):
    db.execute('INSERT OR IGNORE INTO verified_emails VALUES(?,?)', (uid, time.time()))


def _key(path):
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = None
    if descriptor is not None:
        with os.fdopen(descriptor, 'wb') as file:
            file.write(secrets.token_bytes(32))
    # Creation and reading happen under the database write lock, including across processes.
    value = path.read_bytes()
    if len(value) != 32:
        raise VerificationError(503, 'email_send_failed', '邮箱验证暂不可用，请联系管理员')
    return value


def _hash(key, *values):
    return hmac.new(key, '\0'.join(values).encode('utf-8'), hashlib.sha256).hexdigest()


def _subjects(key, email, remote_ip):
    # Do not store raw client IP addresses in rate-limit records.
    return dict(email=_hash(key, 'email', email), ip=_hash(key, 'ip', remote_ip), global_='all')


def _limit(db, kind, subjects, limits, now):
    retry = 0
    for scope, window, maximum in limits:
        subject = subjects['global_' if scope == 'global' else scope]
        count, oldest = db.execute(
            'SELECT COUNT(*),MIN(occurred_at) FROM email_rate_events WHERE kind=? AND subject=? AND occurred_at>?',
            (kind + ':' + scope, subject, now - window)).fetchone()
        if count >= maximum:
            retry = max(retry, max(1, math.ceil(oldest + window - now)))
    if retry:
        raise VerificationError(429, 'email_code_rate_limited', '操作过于频繁，请稍后再试', retry)


def _event(db, kind, subjects, now, include_email=True):
    db.executemany('INSERT INTO email_rate_events VALUES(?,?,?)',
                   [(kind + ':' + scope, subjects[key], now)
                    for scope, key in (('email', 'email'), ('ip', 'ip'), ('global', 'global_'))
                    if include_email or scope != 'email'])


def request_code(db, email, purpose, user_id, remote_ip, key_path):
    email = normalize_email(email)
    if purpose not in ('register', 'verify_email'):
        raise VerificationError(400, 'email_code_invalid', '验证码用途不正确')
    if not mail_delivery.configured():
        raise VerificationError(503, 'email_not_configured', '邮件服务尚未配置，请联系管理员')
    now = time.time()
    challenge_id, code = secrets.token_urlsafe(32), f'{secrets.randbelow(1000000):06d}'
    db.execute('BEGIN IMMEDIATE')
    key = _key(key_path)
    db.execute('DELETE FROM email_rate_events WHERE occurred_at<?', (now - 86400,))
    db.execute('DELETE FROM email_challenges WHERE created_at<?', (now - 86400,))
    subjects = _subjects(key, email, remote_ip)
    _limit(db, 'send', subjects, SEND_LIMITS, now)
    _event(db, 'send', subjects, now)
    db.execute("UPDATE email_challenges SET status='superseded' WHERE email=? AND purpose=? AND user_id=? AND status IN ('pending','active')",
               (email, purpose, user_id))
    db.execute('INSERT INTO email_challenges VALUES(?,?,?,?,?,?,?,?,?)',
               (challenge_id, email, purpose, user_id, _hash(key, challenge_id, email, purpose, user_id, code),
                now, now + CODE_TTL, 0, 'pending'))
    # Reserve quota before network I/O, without holding a database lock while SMTP waits.
    db.commit()
    try:
        mail_delivery.send_verification(email, code, purpose)
    except mail_delivery.DeliveryError as error:
        db.execute("UPDATE email_challenges SET status='failed' WHERE id=?", (challenge_id,))
        db.commit()
        raise VerificationError(503, getattr(error, 'code', 'email_send_failed'), error.message) from None
    db.execute("UPDATE email_challenges SET status='active' WHERE id=? AND status='pending'", (challenge_id,))
    db.commit()
    return dict(challengeId=challenge_id, retryAfter=60, expiresIn=CODE_TTL)


def consume_code(db, email, purpose, user_id, challenge_id, code, remote_ip, key_path):
    """Leave successful consumption in the caller's account transaction."""
    email = normalize_email(email)
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    key, now = _key(key_path), time.time()
    db.execute('DELETE FROM email_rate_events WHERE occurred_at<?', (now - 86400,))
    subjects = _subjects(key, email, remote_ip)
    row = db.execute('SELECT * FROM email_challenges WHERE id=?',
                     (challenge_id if isinstance(challenge_id, str) and len(challenge_id) <= 100 else '',)).fetchone()
    matches = (row and row['email'] == email and row['purpose'] == purpose and row['user_id'] == user_id)
    # An attacker who does not know the unpredictable challenge ID must not be
    # able to exhaust someone else's email budget merely by naming their email.
    limits = GUESS_LIMITS if matches else tuple(limit for limit in GUESS_LIMITS if limit[0] != 'email')
    _limit(db, 'guess', subjects, limits, now)
    expired = matches and row['expires_at'] <= now
    valid = (matches and not expired and row['status'] == 'active' and row['attempts'] < MAX_ATTEMPTS
             and isinstance(code, str) and bool(re.fullmatch(r'[0-9]{6}', code))
             and hmac.compare_digest(row['code_hash'], _hash(key, row['id'], email, purpose, user_id, code)))
    if not valid:
        _event(db, 'guess', subjects, now, include_email=bool(matches))
        if matches:
            db.execute('UPDATE email_challenges SET attempts=attempts+1 WHERE id=?', (row['id'],))
        # Failed attempts must survive the HTTP error transaction's rollback.
        db.commit()
        if expired:
            raise VerificationError(400, 'email_code_expired', '验证码已过期，请重新获取')
        raise VerificationError(400, 'email_code_invalid', '验证码不正确或已失效，请检查或重新获取')
    db.execute("UPDATE email_challenges SET status='consumed' WHERE id=?", (row['id'],))
