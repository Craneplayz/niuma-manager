"""Empty test accounts use normal authentication and disposable storage only."""
import concurrent.futures
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import app
import dev_accounts
import email_verification
from auth_test_support import seed_legacy_account


ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'Disposable-account-2026!'


class DevAccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'data' / 'niuma.sqlite3'
        self.environment = patch.dict(os.environ, NIUMA_ENVIRONMENT='test', NIUMA_DB=str(self.path),
                                      NIUMA_ENABLE_WECHAT='0', NIUMA_ENABLE_MANUAL_MATERIALS='0',
                                      NIUMA_COOKIE_SECURE='0', NIUMA_BACKGROUND_RECOVERY='0',
                                      NIUMA_ADMIN_USER_IDS='')
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.database = patch.object(app, 'DB', self.path)
        self.database.start()
        self.addCleanup(self.database.stop)

    def create(self, email='tester@niuma.test', name='测试账号', password=PASSWORD, expected='test'):
        return dev_accounts.create_account(expected, email, name, password)

    def assert_error(self, code, *args, **kwargs):
        with self.assertRaises(dev_accounts.AccountError) as failure:
            self.create(*args, **kwargs)
        self.assertEqual(failure.exception.code, code)
        self.assertNotIn(PASSWORD, str(failure.exception))

    def child(self, expected='test', environment='test', payload=None, path=None):
        # Never inherit a real database, mail password, or model routing from the
        # shell into a worker exercising environment-boundary rejection.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('NIUMA_', 'DDL_', 'QINIU_', 'TEXT_HARNESS_'))}
        env.update(NIUMA_DB=str(path or self.path), NIUMA_ENABLE_WECHAT='0',
                   NIUMA_ENABLE_MANUAL_MATERIALS='0', NIUMA_BACKGROUND_RECOVERY='0',
                   NIUMA_COOKIE_SECURE='0', NIUMA_ADMIN_USER_IDS='', PYTHONUTF8='1',
                   PYTHONPATH=str(ROOT))
        if environment is not None:
            env['NIUMA_ENVIRONMENT'] = environment
        return subprocess.run([sys.executable, str(ROOT / 'server' / 'dev_accounts.py'), expected],
                              input=json.dumps(payload or dict(email='worker@niuma.test', name='测试账号',
                                                               password=PASSWORD)),
                              cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8',
                              timeout=30)

    def test_creates_only_a_verified_account_without_mail_sessions_or_sample_records(self):
        with patch.object(email_verification.mail_delivery, 'send_verification') as send, \
                patch.object(app, 'initialize') as initialize, \
                patch.object(app, 'initialize_accounts', wraps=app.initialize_accounts) as accounts:
            user = self.create()
        initialize.assert_not_called()
        accounts.assert_called_once()
        send.assert_not_called()
        self.assertEqual(set(user), {'id', 'email', 'name', 'emailVerified'})
        self.assertEqual(user['email'], 'tester@niuma.test')
        self.assertEqual(user['name'], '测试账号')
        self.assertIs(user['emailVerified'], True)
        self.assertNotIn(PASSWORD, json.dumps(user))
        with app.connection() as db:
            stored = db.execute('SELECT * FROM users').fetchall()
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]['id'], user['id'])
            self.assertEqual(stored[0]['hash'], app.password_hash(PASSWORD, stored[0]['salt']))
            self.assertNotEqual(stored[0]['hash'], PASSWORD)
            self.assertEqual(db.execute('SELECT user_id FROM verified_emails').fetchone()[0], user['id'])
            for table in ('sessions', 'workspace', 'proposals', 'email_challenges',
                          'email_rate_events', 'email_reset_credentials'):
                with self.subTest(table=table):
                    self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertFalse((self.path.parent / 'email-verification.key').exists())

    def test_real_worker_creates_an_account_that_logs_in_over_http_with_an_empty_workspace(self):
        process = self.child()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertNotIn(PASSWORD, process.stdout + process.stderr)
        user = json.loads(process.stdout)
        self.assertEqual(set(user), {'id', 'email', 'name', 'emailVerified'})
        self.assertIs(user['emailVerified'], True)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM workspace').fetchone()[0], 0)
        # The account tool builds only account tables; the normal API startup
        # initializes feature schemas before serving login/workspace requests.
        app.initialize(recover=False)
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = build_opener(HTTPCookieProcessor(CookieJar()))

        def call(path, value=None):
            request = Request(f'http://127.0.0.1:{server.server_port}/api{path}',
                              data=None if value is None else json.dumps(value).encode(),
                              headers={'Content-Type': 'application/json'})
            try:
                response = client.open(request, timeout=15)
            except HTTPError as error:
                response = error
            with response:
                return response.status, json.load(response)

        self.assertEqual(call('/auth/me')[0], 401)
        status, result = call('/auth/login', dict(email=user['email'], password=PASSWORD))
        self.assertEqual(status, 200, result)
        self.assertEqual(result, {'user': user})
        self.assertEqual(call('/auth/me'), (200, {'user': user}))
        self.assertEqual(call('/workspace'), (200, {}))
        self.assertEqual(call('/admin/auth/login', dict(email=user['email'], password=PASSWORD))[0], 403)

    def test_worker_rejects_production_unmanaged_and_mismatched_environments_before_creating_storage(self):
        for expected, actual, code in (('production', 'production', 'production_forbidden'),
                                       ('test', 'production', 'production_forbidden'),
                                       ('production', 'test', 'production_forbidden'),
                                       ('test', None, 'environment_mismatch'),
                                       ('test', 'development', 'environment_mismatch'),
                                       ('test', 'staging', 'environment_mismatch')):
            target = self.root / f'{expected}-{actual}' / 'niuma.sqlite3'
            with self.subTest(expected=expected, actual=actual):
                process = self.child(expected, actual, path=target)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(json.loads(process.stdout), {'error': code})
                self.assertNotIn(PASSWORD, process.stdout + process.stderr)
                self.assertFalse(target.parent.exists())

    def test_storage_marker_and_unlabelled_legacy_data_are_never_adopted(self):
        for marker in (None, {'environment': 'production'}, {'environment': 'development'},
                       {'environment': 'test-but-not-really'}):
            folder = self.root / ('legacy' if marker is None else marker['environment'])
            folder.mkdir()
            sentinel = folder / 'niuma.sqlite3'
            sentinel.write_bytes(b'preserve original database exactly')
            if marker is not None:
                (folder / '.niuma-environment.json').write_text(json.dumps(marker))
            before = {entry.name: entry.read_bytes() for entry in folder.iterdir()}
            with self.subTest(marker=marker), patch.dict(os.environ, NIUMA_ADOPT_EXISTING_DATA='1'):
                process = self.child(path=sentinel)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(json.loads(process.stdout), {'error': 'storage_invalid'})
                self.assertEqual({entry.name: entry.read_bytes() for entry in folder.iterdir()}, before)

    def test_in_process_database_override_cannot_bypass_the_selected_environment_path(self):
        other = self.root / 'other' / 'niuma.sqlite3'
        with patch.object(app, 'DB', other), patch.object(app, 'initialize') as initialize:
            self.assert_error('environment_mismatch')
        initialize.assert_not_called()
        self.assertFalse(self.path.exists())
        self.assertFalse(other.exists())

    def test_only_reserved_test_email_addresses_are_accepted_and_idna_is_canonical(self):
        for value in ('person@example.com', 'person@localhost', 'person@test',
                      'person@niuma.test.evil', 'person@niuma.test.', 'a..b@niuma.test',
                      'person@niuma.test\x00', None, 17):
            with self.subTest(email=value):
                self.assert_error('email_invalid', email=value)
        user = self.create(email=' TESTER@例子.TEST ')
        self.assertEqual(user['email'], 'tester@xn--fsqu00a.test')

    def test_names_and_passwords_reject_invalid_types_lengths_and_control_characters(self):
        for value in ('', ' ', 'x' * 41, 'name\nsecond line', 'name\x00', None, 17):
            with self.subTest(name=value):
                self.assert_error('name_invalid', name=value)
        for value in ('', 'short', 'x' * 129, 'password\n', 'password\t', 'password\x7f',
                      'password\x00', None, 17):
            with self.subTest(password_type=type(value).__name__, length=len(value) if isinstance(value, str) else None):
                self.assert_error('password_invalid', password=value)
        self.assertFalse(self.path.exists())

    def test_duplicate_creation_preserves_existing_credentials_verification_and_workspace(self):
        user = self.create()
        with app.connection() as db:
            db.execute('INSERT INTO workspace VALUES(?,?,?,?)', (user['id'], 'tasks', '[{"id":"keep"}]', 7))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', ('existing-session', user['id'], 9999999999))
            before = '\n'.join(db.iterdump())
        self.assert_error('account_exists', email='TESTER@NIUMA.TEST', name='Replacement', password='Replacement-2026!')
        with app.connection() as db:
            self.assertEqual('\n'.join(db.iterdump()), before)

    def test_new_and_duplicate_creation_preserve_expired_receipts_and_uploaded_files(self):
        # A running test API enables manual materials. Its general initializer
        # prunes old receipts and upload files even with recover=False; creating
        # another account must not trigger that maintenance for existing users.
        with patch.dict(os.environ, NIUMA_ENABLE_MANUAL_MATERIALS='1'):
            app.initialize(recover=False)
            owner = self.create(email='existing-owner@niuma.test')
            asset = self.path.parent / 'wechat-assets' / owner['id'] / ('a' * 32)
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b'existing private upload')
            with app.connection() as db:
                db.execute('INSERT INTO source_credentials VALUES(?,?,?)',
                           (owner['id'], 'synthetic-teaching-account', b'synthetic-encrypted-secret'))
                run = app.source_jobs.start(db, owner['id'], dict(
                    contract_version='source-sync/0.1', operation_id='preserved-quiet-check',
                    sources=['teaching_web'], consent=True), app.ApiError)
                app.source_jobs.save(db, run, 'succeeded')
                db.execute('UPDATE source_sync_quiet_runs SET expires_at=0 WHERE run_id=?', (run['run_id'],))
                db.execute('INSERT INTO wechat_assets VALUES(?,?,?,?,?,?,?)',
                           ('asset:' + 'a' * 32, owner['id'], 'private.png', 'image/png',
                            asset.stat().st_size, 'b' * 64, str(asset)))
                db.execute('INSERT INTO wechat_asset_lifetime VALUES(?,0)', ('asset:' + 'a' * 32,))
                tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                                     "AND name NOT IN ('users','verified_emails')")]
                before = {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table}')]
                          for table in tables}
                owner_before = tuple(db.execute('SELECT * FROM users WHERE id=?', (owner['id'],)).fetchone())
                verification_before = tuple(db.execute('SELECT * FROM verified_emails WHERE user_id=?',
                                                       (owner['id'],)).fetchone())
            self.assertEqual(len(before['source_sync_quiet_runs']), 1)
            self.assertEqual(len(before['wechat_assets']), 1)

            with patch.object(app, 'initialize', wraps=app.initialize) as initialize, \
                    patch.object(app.source_jobs, 'initialize', wraps=app.source_jobs.initialize) as source_init, \
                    patch.object(app.wechat_media, 'initialize', wraps=app.wechat_media.initialize) as media_init:
                self.create(email='new-empty-account@niuma.test')
                self.assert_error('account_exists', email=owner['email'])
            initialize.assert_not_called()
            source_init.assert_not_called()
            media_init.assert_not_called()

            with app.connection() as db:
                self.assertEqual({table: [tuple(row) for row in db.execute(f'SELECT * FROM {table}')]
                                  for table in tables}, before)
                self.assertEqual(tuple(db.execute('SELECT * FROM users WHERE id=?',
                                                  (owner['id'],)).fetchone()), owner_before)
                self.assertEqual(tuple(db.execute('SELECT * FROM verified_emails WHERE user_id=?',
                                                  (owner['id'],)).fetchone()), verification_before)
            self.assertEqual(asset.read_bytes(), b'existing private upload')

    def test_legacy_unicode_domain_alias_is_not_replaced_or_marked_verified(self):
        app.initialize(recover=False)
        seed_legacy_account(self.path, dict(email='legacy@例子.test', name='原账号', password=PASSWORD))
        with app.connection() as db:
            before = '\n'.join(db.iterdump())
        self.assert_error('account_exists', email='legacy@xn--fsqu00a.test')
        with app.connection() as db:
            self.assertEqual('\n'.join(db.iterdump()), before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM verified_emails').fetchone()[0], 0)

    def test_verification_write_failure_rolls_back_the_account_insert(self):
        with patch.object(email_verification, 'mark_verified', side_effect=sqlite3.OperationalError('synthetic failure')):
            self.assert_error('create_failed')
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM verified_emails').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM workspace').fetchone()[0], 0)

    def test_concurrent_creation_cannot_duplicate_or_overwrite_the_same_email(self):
        app.initialize(recover=False)
        barrier = threading.Barrier(2)

        def create_once():
            barrier.wait(timeout=10)
            try:
                return self.create()
            except dev_accounts.AccountError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: create_once(), range(2)))
        users = [value for value in results if isinstance(value, dict)]
        self.assertEqual(len(users), 1, results)
        self.assertEqual([value for value in results if isinstance(value, str)], ['account_exists'])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT user_id FROM verified_emails').fetchone()[0], users[0]['id'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
