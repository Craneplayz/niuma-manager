"""Synthetic account fixtures for tests; never used by application request handling."""
import secrets
import sqlite3
from contextlib import closing
from unittest.mock import patch

import app


# Synthetic stored-domain spellings that IDNA maps to the same ASCII identity.
LEGACY_EMAIL_DOMAINS = (
    ('ｅｘａｍｐｌｅ.test', 'example.test'),
    ('example。test', 'example.test'),
    ('example．test', 'example.test'),
    ('example｡test', 'example.test'),
    ('faß.test', 'xn--fa-hia.test'),
    ('ος.test', 'xn--0xag.test'),
    ('bu\u0308cher.test', 'xn--bcher-kva.test'),
)


def registration_payload(account, request_code):
    """Get a real challenge over HTTP while capturing only the synthetic delivery.

    ``request_code`` takes a JSON body and returns the HTTP status and JSON body.
    Mail configuration and sending are mocked only for this request. Non-mail
    business tests do not consume delivery quotas; dedicated email verification
    tests exercise the default limits and all challenge validation unchanged.
    """
    sent = []
    with patch.object(app.email_verification.mail_delivery, 'configured', return_value=True), \
            patch.object(app.email_verification.mail_delivery, 'send_verification',
                         side_effect=lambda email, code, purpose: sent.append((email, code, purpose))), \
            patch.object(app.email_verification, 'SEND_LIMITS', ()):
        status, challenge = request_code(dict(email=account['email'], purpose='register'))
    if status != 200 or len(sent) != 1:
        raise AssertionError(f'Synthetic email challenge failed: HTTP {status}: {challenge}')
    email, code, purpose = sent[0]
    if email != app.email_verification.normalize_email(account['email']) or purpose != 'register':
        raise AssertionError('Synthetic email challenge recipient or purpose mismatch')
    return {**account, 'challengeId': challenge['challengeId'], 'code': code}


def legacy_account_row(account):
    """Build a legacy five-column account only for disposable isolation fixtures."""
    salt = secrets.token_hex(16)
    return (secrets.token_hex(16), account['email'], account['name'], salt,
            app.password_hash(account['password'], salt))


def seed_legacy_account(path, account):
    """Seed a synthetic account into an already initialized disposable test DB."""
    with closing(sqlite3.connect(path, timeout=10)) as db:
        with db:
            db.execute('INSERT INTO users VALUES(?,?,?,?,?)', legacy_account_row(account))
