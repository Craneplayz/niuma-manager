"""Create an empty verified test account through the managed environment CLI."""
import argparse
import json
import os
import secrets
import sqlite3
import sys

import email_verification
import runtime_environment


ERRORS = {
    'production_forbidden': 'Test accounts are disabled in production.',
    'environment_mismatch': 'An explicit matching development/test environment is required.',
    'storage_invalid': 'The data directory is unlabelled, inaccessible or belongs to another environment.',
    'email_invalid': 'Use a valid reserved .test email address for a test account.',
    'name_invalid': 'The display name must contain 1–40 characters.',
    'password_invalid': 'The password must contain 8–128 characters without control characters.',
    'account_exists': 'This account already exists; its password, verification and data were preserved.',
    'create_failed': 'Unable to create the test account; check the selected environment and API version.',
}


class AccountError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(ERRORS[code])


def validate_environment(expected_environment):
    active = os.environ.get('NIUMA_ENVIRONMENT')
    if expected_environment == 'production' or active == 'production':
        raise AccountError('production_forbidden')
    if expected_environment not in ('development', 'test') or active != expected_environment:
        raise AccountError('environment_mismatch')


def validate_identity(email, name):
    try:
        email = email_verification.normalize_email(email)
    except email_verification.VerificationError:
        raise AccountError('email_invalid') from None
    if not email.endswith('.test'):
        raise AccountError('email_invalid')
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 40
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise AccountError('name_invalid')
    return email, name.strip()


def validate_password(password):
    if (not isinstance(password, str) or not 8 <= len(password) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in password)):
        raise AccountError('password_invalid')
    return password


def create_account(expected_environment, email, name, password):
    validate_environment(expected_environment)
    email, name = validate_identity(email, name)
    validate_password(password)
    path = runtime_environment.database_path()
    try:
        runtime_environment.validate_storage(path)
    except (OSError, ValueError):
        raise AccountError('storage_invalid') from None

    # app.DB is fixed on import. The parent starts a separate process with the
    # selected environment before importing the application or opening storage.
    import app
    if app.DB.resolve() != path.resolve():
        raise AccountError('environment_mismatch')
    try:
        with app.connection() as db:
            # Full application initialization also expires receipts/uploads.
            # Account provisioning must never run maintenance for other users.
            app.initialize_accounts(db)
            db.execute('BEGIN IMMEDIATE')
            if email_verification.account_for_email(db, email):
                raise AccountError('account_exists')
            uid, salt = secrets.token_hex(16), secrets.token_hex(16)
            db.execute('INSERT INTO users VALUES(?,?,?,?,?)',
                       (uid, email, name, salt, app.password_hash(password, salt)))
            email_verification.mark_verified(db, uid)
        # No workspace, sessions, administrator roles or demo records are added.
        return dict(id=uid, email=email, name=name, emailVerified=True)
    except (OSError, sqlite3.Error):
        raise AccountError('create_failed') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('environment', choices=runtime_environment.ENVIRONMENTS)
    args = parser.parse_args(argv)
    try:
        # Reject production/unmanaged invocations before reading a password or
        # creating a directory, even if this module is called without the manager.
        validate_environment(args.environment)
        payload = json.loads(sys.stdin.read(4097))
        if not isinstance(payload, dict) or set(payload) != {'email', 'name', 'password'}:
            raise AccountError('create_failed')
        result = create_account(args.environment, **payload)
    except AccountError as error:
        print(json.dumps(dict(error=error.code)))
        return 1
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps(dict(error='create_failed')))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
