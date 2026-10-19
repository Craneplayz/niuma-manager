"""Offline tests for email credential, preview, and sync API routes."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
import email_source
import recognition
import source_sync


ADDRESS = 'student@stu.pku.edu.cn'


class EmailSourceRouteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.key = Path(self.folder.name) / 'credential.key'
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        source_sync.initialize(self.db)
        recognition.initialize(self.db)
        self.responses = []
        self.handler = SimpleNamespace(
            command='POST',
            reply=lambda status, data: self.responses.append((status, data)),
        )

    def tearDown(self):
        self.db.close()
        self.folder.cleanup()

    def call(self, path, body=None, user='owner', method='POST'):
        self.handler.command = method
        return source_sync.route(self.handler, self.db, user, path, body or {}, app.ApiError, self.key)

    def save_credentials(self):
        self.call('/api/sources/email/credentials', {
            'address': ADDRESS.upper(), 'username': ' student ', 'password': 'client secret',
        })

    def test_email_credentials_are_encrypted_account_scoped_and_removable(self):
        self.save_credentials()
        row = self.db.execute('SELECT * FROM email_source_credentials WHERE user_id=?', ('owner',)).fetchone()
        self.assertNotIn(b'clientsecret', row['secret'])
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)

        self.call('/api/sources/email/credentials', method='GET')
        self.assertEqual(self.responses[-1][1], {
            'configured': True, 'address': ADDRESS, 'username': 'student',
        })
        self.call('/api/sources/email/credentials', method='GET', user='other')
        self.assertEqual(self.responses[-1][1], {
            'configured': False, 'address': None, 'username': None,
        })

        self.call('/api/sources/email/credentials/remove')
        self.assertIsNone(self.db.execute('SELECT * FROM email_source_credentials').fetchone())

    def test_preview_decrypts_only_current_users_saved_credential(self):
        self.save_credentials()
        preview = {'messages': [], 'summary': {'total': 0, 'lookback_days': 30}}
        with patch.object(source_sync.email_source, 'preview', return_value=preview) as fetch:
            self.call('/api/sources/email/preview')
        self.assertEqual(fetch.call_args.args, (ADDRESS, 'student', 'clientsecret'))
        self.assertEqual(self.responses[-1], (200, preview))

        with self.assertRaises(app.ApiError) as missing:
            self.call('/api/sources/email/preview', user='other')
        self.assertEqual(missing.exception.status, 400)

    def test_legacy_email_sync_endpoint_is_retired_without_fetching_bodies(self):
        self.save_credentials()
        with patch.object(source_sync.email_source, 'fetch_selected') as fetch:
            for payload in (
                {'sources': ['email'], 'input_ids': ['id']},
                {'sources': ['email'], 'input_ids': ['id', 'id'], 'consent': True},
            ):
                with self.assertRaises(app.ApiError) as retired:
                    self.call('/api/sources/sync', payload)
                self.assertEqual(retired.exception.status, 410)
            fetch.assert_not_called()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM recognition_batches').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
