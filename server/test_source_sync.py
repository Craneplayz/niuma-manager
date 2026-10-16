import json
import hashlib
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
import source_sync
import recognition


class SourceSyncTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.key = Path(self.folder.name) / 'credential.key'
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        source_sync.initialize(self.db)
        recognition.initialize(self.db)
        self.responses = []
        self.handler = SimpleNamespace(command='POST', reply=lambda status, data: self.responses.append((status, data)))

    def tearDown(self):
        self.db.close()
        self.folder.cleanup()

    def call(self, path, body, user='owner'):
        return source_sync.route(self.handler, self.db, user, path, body, app.ApiError, self.key)

    def save(self, username='course-user', password='course-password'):
        self.call('/api/sources/credentials', {'username': username, 'password': password})

    def test_credentials_are_account_scoped_encrypted_and_removable(self):
        with self.assertRaises(app.ApiError) as invalid:
            self.call('/api/sources/credentials', {'username': 'u', 'password': 'line\nbreak'})
        self.assertEqual(invalid.exception.status, 400)
        self.save()
        row = self.db.execute('SELECT * FROM source_credentials').fetchone()
        self.assertNotIn(b'course-password', row['secret'])
        if os.name == 'posix':
            self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)
        self.handler.command = 'GET'
        self.call('/api/sources/credentials', {})
        self.call('/api/sources/credentials', {}, user='other')
        self.assertEqual(self.responses[-2][1], {'configured': True, 'username': 'course-user', 'source_account_id':hashlib.sha256(b'course-user').hexdigest()})
        self.assertEqual(self.responses[-1][1], {'configured': False, 'username': None, 'source_account_id':None})
        self.handler.command = 'POST'
        self.call('/api/sources/credentials/remove', {})
        self.assertIsNone(self.db.execute('SELECT * FROM source_credentials').fetchone())

    def test_sync_uses_temporary_secret_and_stable_request_id(self):
        from ddl_text.tests.test_teaching_pipeline import example
        request, _ = example()
        self.save('中文账号', '密码€')
        with self.assertRaises(app.ApiError) as unsupported:
            self.call('/api/sources/sync', {'sources': ['wechat']})
        self.assertEqual(unsupported.exception.status, 400)
        with self.assertRaises(app.ApiError) as isolated:
            self.call('/api/sources/sync', {'sources': ['teaching_web']}, user='other')
        self.assertEqual(isolated.exception.status, 400)
        crawler = Path(self.folder.name) / 'crawler'
        (crawler / 'node_modules' / 'tsx').mkdir(parents=True)
        secrets = []
        def run(_command, **kwargs):
            self.assertIn('--format=teaching-capture',_command)
            secret = Path(kwargs['env']['PKU_SECRET_PATH'])
            secrets.append(secret)
            self.assertEqual(secret.read_text(encoding='utf-8'), '中文账号\n密码€')
            if os.name == 'posix':
                self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
            self.assertEqual(kwargs['encoding'], 'utf-8')
            self.assertEqual(kwargs['timeout'], 300)
            return SimpleNamespace(returncode=0, stdout=json.dumps(request, ensure_ascii=False) + '\n')
        with patch.object(source_sync, 'CRAWLER', crawler), patch.object(source_sync.subprocess, 'run', side_effect=run):
            self.call('/api/sources/sync', {'sources': ['teaching_web']})
            self.call('/api/sources/sync', {'sources': ['teaching_web']})
        self.assertFalse(any(secret.exists() for secret in secrets))
        self.assertEqual(self.responses[-2][1]['requests'][0]['records'][0]['text'], request['records'][0]['record']['text'])
        self.assertEqual(self.responses[-2][1]['requests'][0]['contract_version'],'teaching-workspace/0.1')
        self.assertTrue(all(len(r['records']) == 1 for r in self.responses[-2][1]['requests']))
        self.assertEqual(self.responses[-2][1]['requests'][0]['request_id'], self.responses[-1][1]['requests'][0]['request_id'])

    def test_temporary_secret_removed_on_timeout(self):
        self.save()
        crawler = Path(self.folder.name) / 'crawler'
        (crawler / 'node_modules' / 'tsx').mkdir(parents=True)
        paths = []
        def timeout(_command, **kwargs):
            paths.append(Path(kwargs['env']['PKU_SECRET_PATH']))
            self.assertEqual(kwargs['timeout'], 300)
            raise subprocess.TimeoutExpired('crawler', 300)
        with patch.object(source_sync, 'CRAWLER', crawler), patch.object(source_sync.subprocess, 'run', side_effect=timeout):
            with self.assertRaises(app.ApiError) as failed:
                self.call('/api/sources/sync', {'sources': ['teaching_web']})
        self.assertEqual(failed.exception.status, 503)
        self.assertFalse(paths[0].exists())
