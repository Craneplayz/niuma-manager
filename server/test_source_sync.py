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

    def test_collection_causes_are_allowlisted_and_old_crawlers_remain_compatible(self):
        from contracts.tests.test_collection_failure import EXAMPLES
        from source_jobs import collection_error
        self.save()
        crawler = Path(self.folder.name) / 'crawler'
        (crawler / 'node_modules' / 'tsx').mkdir(parents=True)
        for event in EXAMPLES:
            result = SimpleNamespace(returncode=1, stdout='', stderr='PRIVATE_PATH_URL_PASSWORD\n' + json.dumps(event))
            with patch.object(source_sync, 'CRAWLER', crawler), patch.object(source_sync.subprocess, 'run', return_value=result):
                with self.assertRaises(app.ApiError) as caught:
                    self.call('/api/sources/sync', {'sources': ['teaching_web']})
            receipt = collection_error(caught.exception, app.ApiError)
            self.assertEqual(receipt['code'], 'collection_failed')
            self.assertEqual(receipt['collection_failure'], event)
            self.assertNotIn('PRIVATE', json.dumps(receipt))
        for stderr in ('PRIVATE_ACCOUNT_PASSWORD', json.dumps(dict(EXAMPLES[0], body='PRIVATE'))):
            result = SimpleNamespace(returncode=1, stdout='', stderr=stderr)
            with patch.object(source_sync, 'CRAWLER', crawler), patch.object(source_sync.subprocess, 'run', return_value=result):
                with self.assertRaises(app.ApiError) as caught:
                    self.call('/api/sources/sync', {'sources': ['teaching_web']})
            receipt = collection_error(caught.exception, app.ApiError)
            self.assertNotIn('collection_failure', receipt)
            self.assertNotIn('PRIVATE', json.dumps(receipt))

    def test_export_and_launch_failures_keep_distinct_causes(self):
        self.save()
        crawler = Path(self.folder.name) / 'crawler'
        (crawler / 'node_modules' / 'tsx').mkdir(parents=True)
        for failure, stage, code in ((OSError('PRIVATE'), 'browser', 'operation_failed'),
                                     (subprocess.TimeoutExpired('PRIVATE', 300), 'collection', 'timeout')):
            with patch.object(source_sync, 'CRAWLER', crawler), patch.object(source_sync.subprocess, 'run', side_effect=failure):
                with self.assertRaises(app.ApiError) as caught:
                    self.call('/api/sources/sync', {'sources': ['teaching_web']})
            self.assertEqual(caught.exception.collection_failure['stage'], stage)
            self.assertEqual(caught.exception.collection_failure['code'], code)
            self.assertNotIn('PRIVATE', caught.exception.message)
        result = SimpleNamespace(returncode=0, stdout='PRIVATE_INVALID_JSON')
        with patch.object(source_sync, 'CRAWLER', crawler), patch.object(source_sync.subprocess, 'run', return_value=result):
            with self.assertRaises(app.ApiError) as caught:
                self.call('/api/sources/sync', {'sources': ['teaching_web']})
        self.assertEqual(caught.exception.collection_failure['stage'], 'export')
        self.assertEqual(self.db.execute('SELECT count(*) FROM teaching_sources').fetchone()[0], 0)

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

    def test_scoped_collection_only_registers_and_reads_selected_exception_assets(self):
        from ddl_text.tests.test_teaching_pipeline import example
        request,_=example(('explicit-complete',)*2)
        self.save();crawler=Path(self.folder.name)/'crawler';(crawler/'node_modules'/'tsx').mkdir(parents=True)
        data=b'synthetic captured asset';digest=hashlib.sha256(data).hexdigest()
        selected=request['records'][0]['record']['input_id']
        for entry in request['records']:
            entry['record']['references']=[dict(ref_id='file:one',kind='attachment',label='合成图片.png',read_state='unread',text=None)]
        def capture(*args,**kwargs):
            folder=Path(kwargs['env']['PKU_ATTACHMENT_DIR'])
            (folder/(digest+'.bin')).write_bytes(data)
            receipts=[dict(input_id=entry['record']['input_id'],ref_id='file:one',sha256=digest if index==0 else '0'*64,mime_type='image/png',size_bytes=len(data)) for index,entry in enumerate(request['records'])]
            # The unselected asset is deliberately absent: it must not be read.
            (folder/'manifest.json').write_text(json.dumps(receipts))
            return SimpleNamespace(returncode=0,stdout=json.dumps(request))
        with patch.object(source_sync,'CRAWLER',crawler),patch.object(source_sync.subprocess,'run',side_effect=capture):
            result=source_sync.collect(self.db,'owner',app.ApiError,self.key,input_ids={selected})
        self.assertEqual([item['records'][0]['input_id'] for item in result['requests']],[selected])
        self.assertEqual([row['input_id'] for row in self.db.execute('SELECT input_id FROM teaching_sources')],[selected])
        self.assertEqual(self.db.execute('SELECT count(*) FROM teaching_asset_files').fetchone()[0],1)
