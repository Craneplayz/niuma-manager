import copy
import hashlib
import json
import tempfile
import time
import threading
import subprocess
import sys
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from unittest.mock import patch

import app
import wechat_media
from contracts.check_v031_examples import check_pair


class WechatMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app.DB = Path(cls.tmp.name) / 'wechat.sqlite3'
        app.initialize()
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port) + '/api'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.tmp.cleanup()

    def client(self, email):
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        self.call(client, '/auth/register', 'POST', dict(name='Test', email=email, password='Test-password-2026'))
        return client

    def call(self, client, path, method='GET', body=None, token=None, multipart=False, fields=None):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        if multipart:
            boundary = 'niuma-test-boundary'
            prefix = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode('utf-8') for key,value in (fields or {}).items())
            data = prefix + b'--' + boundary.encode() + b'\r\nContent-Disposition: form-data; name="file"; filename="work.png"\r\nContent-Type: image/png\r\n\r\n' + body + b'\r\n--' + boundary.encode() + b'--\r\n'
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        else:
            data = json.dumps(body).encode() if body is not None else None
            if data is not None: headers['Content-Type'] = 'application/json'
        try:
            response = client.open(Request(self.base + path, data, headers, method=method))
        except HTTPError as error:
            response = error
        raw=response.read()
        return response.status, json.loads(raw) if raw else {}

    def test_upload_scope_import_idempotency_and_failure_is_not_no_tasks(self):
        a, b = self.client('wechat-a@test.local'), self.client('wechat-b@test.local')
        status, credential = self.call(a, '/sources/wechat/credential', 'POST', {})
        self.assertEqual(status, 200)
        token = credential['token']
        self.assertEqual(self.call(b, '/sources/wechat/assets', 'POST', b'\x89PNG\r\n\x1a\nabc', token=token, multipart=True)[0], 201)
        raw = b'\x89PNG\r\n\x1a\nabc'
        status, ack = self.call(a, '/sources/wechat/assets', 'POST', raw, token=token, multipart=True)
        self.assertEqual(status, 200)
        request = json.loads((Path(__file__).parent.parent / 'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        asset = request['records'][0]['attachments'][0]
        asset.update({key:ack[key] for key in ('file_ref','file_name','mime_type','size_bytes','sha256')})
        self.assertEqual(self.call(b, '/sources/wechat/imports', 'POST', request)[0], 404)
        status, submitted = self.call(a, '/sources/wechat/imports', 'POST', request)
        self.assertEqual(status, 202)
        self.assertEqual(self.call(a, '/sources/wechat/imports', 'POST', request)[0], 200)
        same = copy.deepcopy(request); same['request_id'] = 'another-collection'; same['records'][0]['collected_at'] = '2026-10-02T20:05:00+08:00'
        self.assertNotEqual(self.call(a, '/sources/wechat/imports', 'POST', same)[1]['job_id'], submitted['job_id'])
        changed = copy.deepcopy(request); changed['records'][0]['source_label'] = 'different'
        self.assertEqual(self.call(a, '/sources/wechat/imports', 'POST', changed)[0], 409)
        changed = copy.deepcopy(request); changed['records'][0]['collected_at'] = '2026-10-02T20:05:00+08:00'
        self.assertEqual(self.call(a, '/sources/wechat/imports', 'POST', changed)[0], 409)
        self.assertEqual(self.call(b, '/sources/wechat/imports/' + submitted['job_id'])[0], 404)
        for _ in range(100):
            status, job = self.call(a, '/sources/wechat/imports/' + submitted['job_id'])
            if job['status'] == 'failed': break
            time.sleep(.02)
        self.assertEqual(status, 200)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['result']['status'], 'failed')
        self.assertNotEqual(job['result']['status'], 'no_tasks')
        check_pair(request, job['result'])
        status, retry = self.call(a, '/sources/wechat/imports/' + submitted['job_id'] + '/retry', 'POST', dict(retry_id='retry-once'))
        self.assertEqual(status, 409)
        self.assertEqual(self.call(a, '/sources/wechat/credential/revoke', 'POST', {})[0], 200)
        self.assertEqual(self.call(a, '/sources/wechat/imports/' + submitted['job_id'], token=token)[0], 401)

    def test_asset_loader_rechecks_ownership_bytes_and_deleted_handles(self):
        client = self.client('wechat-loader@test.local')
        raw = b'\x89PNG\r\n\x1a\nloader'
        _, ack = self.call(client,'/sources/wechat/assets','POST',raw,multipart=True)
        with app.connection() as db:
            uid = db.execute('SELECT id FROM users WHERE email=?',('wechat-loader@test.local',)).fetchone()['id']
            path = Path(db.execute('SELECT path FROM wechat_assets WHERE ref=?',(ack['file_ref'],)).fetchone()['path'])
        loader = wechat_media.account_asset_loader(app.DB,uid)
        self.assertEqual(loader(ack['file_ref'],max_bytes=100,timeout_seconds=1),raw)
        with self.assertRaises(FileNotFoundError):
            wechat_media.account_asset_loader(app.DB,'other-account')(ack['file_ref'],max_bytes=100,timeout_seconds=1)
        with self.assertRaises(ValueError):
            loader(ack['file_ref'],max_bytes=2,timeout_seconds=1)
        with self.assertRaises(TimeoutError), patch.object(wechat_media.time,'monotonic',side_effect=[0,2]):
            loader(ack['file_ref'],max_bytes=100,timeout_seconds=1)
        # Simulate a corrupt storage object without changing the persisted receipt.
        with path.open('ab') as target:
            target.write(b'changed')
        with self.assertRaises(ValueError):
            loader(ack['file_ref'],max_bytes=100,timeout_seconds=1)
        with app.connection() as db:
            db.execute('DELETE FROM wechat_assets WHERE ref=?',(ack['file_ref'],))
        with self.assertRaises(FileNotFoundError):
            loader(ack['file_ref'],max_bytes=100,timeout_seconds=1)

    def test_collector_multipart_accepts_only_one_file_part(self):
        client = self.client('wechat-multipart@test.local')
        raw = b'\x89PNG\r\n\x1a\ncollector-body'
        status, ack = self.call(client,'/sources/wechat/assets','POST',raw,multipart=True)
        self.assertEqual(status,201)
        self.assertEqual(ack['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(ack['size_bytes'],len(raw))
        self.assertEqual(self.call(client,'/sources/wechat/assets','POST',raw,multipart=True)[0],200)
        for key in ('contract_version','file_name','mime_type','size_bytes','sha256','user_id','file'):
            status, response = self.call(client,'/sources/wechat/assets','POST',raw,multipart=True,fields={key:'extra'})
            self.assertEqual(status,400)
            self.assertEqual(response['error']['code'],'invalid_request')
    def test_shared_reader_receives_only_the_bound_loader_and_model_configuration(self):
        try:
            from ddl_text.media_pipeline import process_media_request
        except ImportError:
            self.skipTest('image runtime requires Python 3.10+ and media dependencies')
        request = {'request_id':'adapter-wiring'}
        loader = lambda *args,**kwargs:b'account-scoped-bytes'
        options = dict(provider='qiniu',key='synthetic-key',model='test-image-model',endpoint='https://example.invalid/v1/chat/completions')
        with patch('ddl_text.codex_client.live_options',return_value=options), patch('ddl_text.media_pipeline.process_media_request',return_value={'status':'success'}) as adapter:
            self.assertEqual(wechat_media.recognize_v03(request,loader),{'status':'success'})
            arguments = adapter.call_args.args
            self.assertIs(arguments[1],loader)
            self.assertEqual(arguments[3].model,'test-image-model')
            self.assertEqual(arguments[3].max_retries,0)

    def test_expiration_removes_only_unreferenced_uploads_and_retains_job_files(self):
        client = self.client('wechat-ttl@test.local')
        _, loose = self.call(client,'/sources/wechat/assets','POST',b'\x89PNG\r\n\x1a\nloose',multipart=True)
        _, held = self.call(client,'/sources/wechat/assets','POST',b'\x89PNG\r\n\x1a\nheld',multipart=True)
        with app.connection() as db:
            paths = {row['ref']:Path(row['path']) for row in db.execute('SELECT * FROM wechat_assets WHERE ref IN (?,?)',(loose['file_ref'],held['file_ref']))}
            db.execute('UPDATE wechat_asset_lifetime SET uploaded_at=0 WHERE ref IN (?,?)',(loose['file_ref'],held['file_ref']))
            wechat_media._retain_assets(db,'retained-ttl-job',{'records':[{'attachments':[{'file_ref':held['file_ref']}]}]})
            wechat_media._expire_unreferenced(db,now=100000)
            self.assertIsNone(db.execute('SELECT 1 FROM wechat_assets WHERE ref=?',(loose['file_ref'],)).fetchone())
            self.assertIsNotNone(db.execute('SELECT 1 FROM wechat_assets WHERE ref=?',(held['file_ref'],)).fetchone())
        self.assertFalse(paths[loose['file_ref']].exists())
        self.assertTrue(paths[held['file_ref']].exists())

    def test_uploaded_pixels_run_through_shared_reader_and_persist_review_candidate(self):
        try:
            from ddl_text.tests.media_cases import FakeMediaClient, image_bytes, request_for
        except ImportError:
            self.skipTest('image runtime dependencies unavailable')
        client = self.client('wechat-pixel-pipeline@test.local')
        pixels = image_bytes()
        request, _ = request_for(pixels)
        request['request_id'] = 'uploaded-pixel-pipeline'
        _, receipt = self.call(client,'/sources/wechat/assets','POST',pixels,multipart=True)
        request['records'][0]['attachments'][0].update({key:receipt[key] for key in ('file_ref','file_name','mime_type','size_bytes','sha256')})
        options = dict(provider='qiniu',key='synthetic-key',model='test-image-model',endpoint='https://example.invalid/v1/chat/completions')
        with patch('ddl_text.codex_client.live_options',return_value=options), patch('ddl_text.chat_client.ChatClient',return_value=FakeMediaClient()):
            status, ack = self.call(client,'/sources/wechat/imports','POST',request)
            self.assertEqual(status,202)
            for _ in range(200):
                _, job = self.call(client,'/sources/wechat/imports/'+ack['job_id'])
                if job['status'] not in ('queued','running'): break
                time.sleep(.05)
        self.assertEqual(job['status'],'succeeded',job)
        check_pair(request,job['result'])
        summary = job['result']['record_results'][0]['attachments'][0]
        self.assertEqual(summary['coverage_basis'],'adapter_confirmed')
        self.assertTrue(job['result']['tasks'][0]['needs_review'])
        batch = self.call(client,'/recognition/batches')[1][0]
        self.assertEqual(batch['items'][0]['state'],'pending')
        self.assertEqual(self.call(client,'/workspace')[1].get('tasks',{}).get('value',[]),[])
        self.assertNotIn(receipt['file_ref'],json.dumps(job['result']))

    def test_model_reported_candidates_require_confirmation_and_stay_owned(self):
        a, b = self.client('wechat-success@test.local'), self.client('wechat-success-other@test.local')
        raw = b'\x89PNG\r\n\x1a\nnew-image'
        _, ack = self.call(a, '/sources/wechat/assets', 'POST', raw, multipart=True)
        request = json.loads((Path(__file__).parent.parent / 'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        request['request_id'] = 'wechat-success-test'
        asset = request['records'][0]['attachments'][0]
        asset.update({key:ack[key] for key in ('file_ref','file_name','mime_type','size_bytes','sha256')})
        result = json.loads((Path(__file__).parent.parent / 'contracts/text-to-app/text-attachments-v031-output.json').read_text(encoding='utf-8'))
        result['request_id'] = request['request_id']
        with patch.object(wechat_media, 'recognize_v03', return_value=result):
            status, submitted = self.call(a, '/sources/wechat/imports', 'POST', request)
            self.assertEqual(status, 202)
            for _ in range(100):
                _, job = self.call(a, '/sources/wechat/imports/' + submitted['job_id'])
                if job['status'] == 'succeeded': break
                time.sleep(.02)
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(len(self.call(a, '/workspace')[1].get('tasks',{}).get('value',[])), 0)
        batch = self.call(a, '/recognition/batches')[1][0]
        candidate = batch['items'][0]
        self.assertTrue(candidate['candidate']['needs_review'])
        self.assertEqual(candidate['state'], 'pending')
        resolution = dict(action='create',version=candidate['version'],tasks_version=0,reviewed=True,
                          draft=dict(title='提交实验报告',description='已核对附件',course='计算机系统导论',
                                     due=dict(date='2026-10-05',time='09:00',precision='datetime')))
        self.assertEqual(self.call(b, '/recognition/candidates/'+candidate['id']+'/resolve', 'POST',resolution)[0],404)
        self.assertEqual(self.call(a, '/recognition/candidates/'+candidate['id']+'/resolve', 'POST',resolution)[0],200)
        self.assertEqual(len(self.call(b, '/workspace')[1].get('tasks', {}).get('value', [])), 0)
        self.assertEqual(self.call(a, '/sources/wechat/imports', 'POST', request)[0], 200)
        self.assertEqual(len(self.call(a, '/workspace')[1]['tasks']['value']), 1)
        self.assertEqual(len(self.call(a, '/sources/wechat/imports')[1]['jobs']), 1)

    def test_failed_retry_envelope_preserves_candidates_and_retry_history(self):
        client = self.client('wechat-retry-history@test.local')
        cases = json.loads((Path(__file__).parent.parent / 'contracts/examples/text-attachments-v031-cases.json').read_text(encoding='utf-8'))
        case = next(item for item in cases if item['name'] == 'recognition-timeout-preserves-first-task')
        request, result = copy.deepcopy(case['request']), copy.deepcopy(case['expected'])
        request['request_id'] = 'retry-history'
        result['request_id'] = request['request_id']
        def finished(job_id):
            for _ in range(100):
                _, job = self.call(client, '/sources/wechat/imports/'+job_id)
                if job['status'] not in ('queued','running'): return job
                time.sleep(.02)
            self.fail('job did not finish')
        with patch.object(wechat_media, 'recognize_v03', return_value=result):
            _, ack = self.call(client, '/sources/wechat/imports', 'POST', request)
            original = finished(ack['job_id'])
        batch = self.call(client, '/recognition/batches')[1][0]
        item = batch['items'][0]
        self.assertEqual(self.call(client, '/recognition/candidates/'+item['id']+'/resolve','POST',dict(action='ignore',version=item['version']))[0],200)
        with patch.object(wechat_media, 'recognize_v03', side_effect=lambda body,assets,**kwargs:wechat_media._failed_result(body)) as model:
            for retry_id in ['retry-A','retry-B']:
                self.assertEqual(self.call(client, '/sources/wechat/imports/'+ack['job_id']+'/retry','POST',dict(retry_id=retry_id))[0],202)
                after = finished(ack['job_id'])
                self.assertEqual(after['result']['tasks'],original['result']['tasks'])
            self.assertEqual(self.call(client, '/sources/wechat/imports/'+ack['job_id']+'/retry','POST',dict(retry_id='retry-A'))[0],200)
            self.assertEqual(model.call_count,2)
        self.assertEqual(self.call(client, '/recognition/batches')[1][0]['items'][0]['state'],'ignored')
        self.assertEqual(self.call(client, '/sources/wechat/imports')[1]['jobs'][0]['last_attempt_errors'][0]['code'],'agent_unavailable')
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT attempts FROM wechat_jobs WHERE id=?',(ack['job_id'],)).fetchone()['attempts'],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wechat_retries WHERE job_id=?',(ack['job_id'],)).fetchone()[0],2)

    def test_synthetic_file_rejects_spoofed_metadata_and_missing_reference(self):
        client = self.client('wechat-invalid@test.local')
        self.assertEqual(self.call(client, '/sources/wechat/assets', 'POST', b'not-a-png', multipart=True)[0], 400)
        _, ack = self.call(client, '/sources/wechat/assets', 'POST', b'\x89PNG\r\n\x1a\nsynthetic', multipart=True)
        request = json.loads((Path(__file__).parent.parent / 'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        asset = request['records'][0]['attachments'][0]
        asset.update({key: ack[key] for key in ('file_ref', 'file_name', 'mime_type', 'size_bytes', 'sha256')})
        altered = copy.deepcopy(request)
        altered['records'][0]['attachments'][0]['sha256'] = '0' * 64
        self.assertEqual(self.call(client, '/sources/wechat/imports', 'POST', altered)[0], 400)
        missing = copy.deepcopy(request)
        missing['records'][0]['attachments'][0]['file_ref'] = 'asset:' + 'A' * 32
        self.assertEqual(self.call(client, '/sources/wechat/imports', 'POST', missing)[0], 404)

    def test_restart_recovers_a_running_job_without_losing_it(self):
        a = self.client('wechat-recover@test.local')
        request = json.loads((Path(__file__).parent.parent / 'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        request['request_id'] = 'recover-synthetic'
        with app.connection() as db:
            uid = db.execute('SELECT id FROM users WHERE email=?', ('wechat-recover@test.local',)).fetchone()['id']
            db.execute('INSERT INTO wechat_jobs VALUES(?,?,?,?,?,?,?,?,?,?)', ('job-recover',uid,request['request_id'],'fingerprint',json.dumps(request),'running',None,None,None,0))
        app.initialize()
        for _ in range(100):
            _, job = self.call(a, '/sources/wechat/imports/job-recover')
            if job['status'] == 'failed': break
            time.sleep(.02)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['progress']['finished_records'], 1)

    def test_partial_retry_keeps_candidate_and_only_retries_failed_record(self):
        a = self.client('wechat-partial@test.local')
        cases = json.loads((Path(__file__).parent.parent / 'contracts/examples/text-attachments-v031-cases.json').read_text(encoding='utf-8'))
        case = next(item for item in cases if item['name'] == 'recognition-timeout-preserves-first-task')
        request, partial = case['request'], case['expected']
        complete = copy.deepcopy(partial)
        complete['status'] = 'success'; complete['errors'] = []
        complete['record_results'][0]['status'] = 'success'
        complete['record_results'][0]['coverage']['complete'] = True
        check_pair(request, complete)
        with patch.object(wechat_media, 'recognize_v03', return_value=partial):
            status, submitted = self.call(a, '/sources/wechat/imports', 'POST', request)
            self.assertEqual(status, 202)
            for _ in range(100):
                _, job = self.call(a, '/sources/wechat/imports/' + submitted['job_id'])
                if job['status'] == 'partial_failed': break
                time.sleep(.02)
        self.assertEqual(job['status'], 'partial_failed')
        self.assertEqual(job['result']['tasks'][0]['candidate_id'], partial['tasks'][0]['candidate_id'])
        with patch.object(wechat_media, 'recognize_v03', return_value=complete) as model:
            self.assertEqual(self.call(a, '/sources/wechat/imports/' + submitted['job_id'] + '/retry', 'POST', dict(retry_id='partial-retry'))[0], 202)
            for _ in range(100):
                _, job = self.call(a, '/sources/wechat/imports/' + submitted['job_id'])
                if job['status'] == 'succeeded': break
                time.sleep(.02)
            self.assertEqual(model.call_count, 1)
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(len(job['result']['tasks']), 1)
        self.assertEqual(job['result']['tasks'][0]['candidate_id'], partial['tasks'][0]['candidate_id'])


    def test_content_cache_keeps_new_request_identity_and_old_user_decisions(self):
        client = self.client('wechat-content-cache@test.local')
        _, receipt = self.call(client,'/sources/wechat/assets','POST',b'\x89PNG\r\n\x1a\ncache',multipart=True)
        root = Path(__file__).parent.parent
        request = json.loads((root/'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        request['request_id'] = 'content-cache-first'
        request['records'][0]['attachments'][0].update({key:receipt[key] for key in ('file_ref','file_name','mime_type','size_bytes','sha256')})
        result = json.loads((root/'contracts/text-to-app/text-attachments-v031-output.json').read_text(encoding='utf-8'))
        def model(body,loader,**kwargs):
            return dict(result,request_id=body['request_id'])
        def finish(job_id):
            for _ in range(100):
                _, job = self.call(client,'/sources/wechat/imports/'+job_id)
                if job['status'] not in ('queued','running'): return job
                time.sleep(.02)
            self.fail('cache job did not finish')
        options = dict(provider='qiniu',key='synthetic-key',model='cache-model',endpoint='https://example.invalid/v1/chat/completions')
        with patch('ddl_text.codex_client.live_options',return_value=options),patch.object(wechat_media,'recognize_v03',side_effect=model) as adapter:
            _, first = self.call(client,'/sources/wechat/imports','POST',request)
            self.assertEqual(finish(first['job_id'])['status'],'succeeded')
            item = self.call(client,'/recognition/batches')[1][0]['items'][0]
            self.call(client,'/recognition/candidates/'+item['id']+'/resolve','POST',dict(action='ignore',version=item['version']))
            recollected = copy.deepcopy(request)
            recollected['request_id'] = 'content-cache-recollected'
            recollected['records'][0]['collected_at'] = '2026-10-07T10:00:00+08:00'
            _, second = self.call(client,'/sources/wechat/imports','POST',recollected)
            cached = finish(second['job_id'])
            self.assertNotEqual(first['job_id'],second['job_id'])
            self.assertEqual(second['request_id'],recollected['request_id'])
            self.assertEqual(cached['result']['request_id'],recollected['request_id'])
            check_pair(recollected,cached['result'])
            self.assertEqual(adapter.call_count,1)
            jobs = self.call(client,'/sources/wechat/imports')[1]['jobs']
            self.assertEqual(jobs[0]['items'][0]['id'],item['id'])
            self.assertEqual(jobs[0]['items'][0]['state'],'ignored')
            batches=self.call(client,'/recognition/batches')[1]
            self.assertEqual(len(batches),2)
            self.assertEqual(sum(len(batch['items']) for batch in batches),1)
            options['model'] = 'new-cache-model'
            upgraded = dict(recollected,request_id='content-cache-new-policy')
            _, third = self.call(client,'/sources/wechat/imports','POST',upgraded)
            self.assertEqual(finish(third['job_id'])['status'],'succeeded')
            self.assertEqual(adapter.call_count,2)

    def test_content_key_includes_message_context_publication_and_policy_but_not_handles(self):
        request = json.loads((Path(__file__).parent.parent/'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        options = dict(provider='qiniu',key='synthetic-key',model='key-model',endpoint='https://example.invalid/v1/chat/completions')
        with patch('ddl_text.codex_client.live_options',return_value=options):
            key = wechat_media._content_key(request)
            changed = copy.deepcopy(request);changed['request_id']='other-id'
            changed['records'][0]['collected_at']='2026-10-07T10:00:00+08:00'
            changed['records'][0]['attachments'][0]['file_ref']='asset:resigned_handle_1234567890'
            self.assertEqual(wechat_media._content_key(changed),key)
            for field in ('input_id','source_item_id','text','source_label'):
                changed = copy.deepcopy(request);changed['records'][0][field]='different-value'
                self.assertNotEqual(wechat_media._content_key(changed),key)
            changed = copy.deepcopy(request);changed['records'][0]['context'][0]['text']='different-context'
            self.assertNotEqual(wechat_media._content_key(changed),key)
            changed = copy.deepcopy(request);changed['records'][0]['attachments'][0]['sha256']='0'*64
            self.assertNotEqual(wechat_media._content_key(changed),key)
            options['key']='rotated-secret'
            self.assertEqual(wechat_media._content_key(request),key)

    def test_source_removal_invalidates_handles_and_cannot_revive_a_late_result(self):
        client = self.client('wechat-source-remove@test.local')
        other = self.client('wechat-source-keep@test.local')
        raw = b'\x89PNG\r\n\x1a\nremove-source'
        _, receipt = self.call(client,'/sources/wechat/assets','POST',raw,multipart=True)
        _, other_receipt = self.call(other,'/sources/wechat/assets','POST',raw,multipart=True)
        _, credential = self.call(client,'/sources/wechat/credential','POST',{})
        root = Path(__file__).parent.parent
        request = json.loads((root/'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        request['request_id']='remove-running-source'
        request['records'][0]['attachments'][0].update({key:receipt[key] for key in ('file_ref','file_name','mime_type','size_bytes','sha256')})
        result = json.loads((root/'contracts/text-to-app/text-attachments-v031-output.json').read_text(encoding='utf-8'))
        result['request_id']=request['request_id']
        started, release, finished = threading.Event(),threading.Event(),threading.Event()
        def model(body,loader,**kwargs):
            started.set();release.wait(5)
            return result
        original_process=wechat_media._process
        def worker(*args):
            try: original_process(*args)
            finally: finished.set()
        with app.connection() as db:
            uid=db.execute('SELECT id FROM users WHERE email=?',('wechat-source-remove@test.local',)).fetchone()['id']
            path=Path(db.execute('SELECT path FROM wechat_assets WHERE ref=?',(receipt['file_ref'],)).fetchone()['path'])
            db.execute('INSERT INTO workspace VALUES(?,?,?,?)',(uid,'tasks',json.dumps([dict(id='keep-user-task',title='已确认任务')]),1))
        with patch.object(wechat_media,'recognize_v03',side_effect=model),patch.object(wechat_media,'_process',side_effect=worker):
            _, ack=self.call(client,'/sources/wechat/imports','POST',request)
            self.assertTrue(started.wait(3))
            self.assertEqual(self.call(client,'/sources/wechat/remove','POST',{})[0],400)
            self.assertEqual(self.call(client,'/sources/wechat/remove','POST',dict(confirmed=True),token=credential['token'])[0],403)
            status,deleted=self.call(client,'/sources/wechat/remove','POST',dict(confirmed=True))
            self.assertEqual(status,200)
            self.assertEqual(deleted['cleanup_pending'],0)
            release.set();self.assertTrue(finished.wait(3))
        self.assertFalse(path.exists())
        self.assertEqual(self.call(client,'/sources/wechat/imports/'+ack['job_id'])[0],404)
        self.assertEqual(self.call(client,'/sources/wechat/imports')[1]['jobs'],[])
        self.assertEqual(self.call(client,'/recognition/batches')[1],[])
        self.assertEqual(self.call(client,'/workspace')[1]['tasks']['value'][0]['id'],'keep-user-task')
        self.assertEqual(self.call(client,'/sources/wechat/imports',token=credential['token'])[0],401)
        with self.assertRaises(FileNotFoundError):
            wechat_media.account_asset_loader(app.DB,uid)(receipt['file_ref'],max_bytes=100,timeout_seconds=1)
        with app.connection() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM wechat_assets WHERE ref=?',(other_receipt['file_ref'],)).fetchone())

    def test_deleted_account_and_locked_file_cleanup_are_recovered_on_startup(self):
        client = self.client('wechat-deleted-account@test.local')
        _,receipt=self.call(client,'/sources/wechat/assets','POST',b'\x89PNG\r\n\x1a\naccount-cleanup',multipart=True)
        _,credential=self.call(client,'/sources/wechat/credential','POST',{})
        with app.connection() as db:
            uid=db.execute('SELECT id FROM users WHERE email=?',('wechat-deleted-account@test.local',)).fetchone()['id']
            path=Path(db.execute('SELECT path FROM wechat_assets WHERE ref=?',(receipt['file_ref'],)).fetchone()['path'])
            db.execute('DELETE FROM users WHERE id=?',(uid,))
            with patch.object(Path,'unlink',side_effect=PermissionError('synthetic locked file')):
                wechat_media.initialize(db)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wechat_pending_deletes WHERE user_id=?',(uid,)).fetchone()[0],1)
        self.assertEqual(self.call(client,'/sources/wechat/imports',token=credential['token'])[0],401)
        with app.connection() as db:
            wechat_media.initialize(db)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wechat_pending_deletes WHERE user_id=?',(uid,)).fetchone()[0],0)
        self.assertFalse(path.exists())

    def test_asset_revocation_is_owner_idempotent_and_reupload_signs_a_new_handle(self):
        client=self.client('wechat-revoke-asset@test.local')
        other=self.client('wechat-revoke-other@test.local')
        raw=b'\x89PNG\r\n\x1a\nrevoke-one-file'
        _,receipt=self.call(client,'/sources/wechat/assets','POST',raw,multipart=True)
        path='/sources/wechat/assets/'+receipt['file_ref']
        _,credential=self.call(client,'/sources/wechat/credential','POST',{})
        self.assertEqual(self.call(client,path,'DELETE',token=credential['token'])[0],403)
        self.assertEqual(self.call(other,path,'DELETE')[0],404)
        self.assertEqual(self.call(client,path,'DELETE')[0],204)
        self.assertEqual(self.call(client,path,'DELETE')[0],204)
        status,reuploaded=self.call(client,'/sources/wechat/assets','POST',raw,multipart=True)
        self.assertEqual(status,201)
        self.assertNotEqual(receipt['file_ref'],reuploaded['file_ref'])
        self.assertEqual(self.call(client,path,'DELETE')[0],204)
        root=Path(__file__).parent.parent
        request=json.loads((root/'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        request['records'][0]['attachments'][0].update({key:receipt[key] for key in ('file_ref','file_name','mime_type','size_bytes','sha256')})
        self.assertEqual(self.call(client,'/sources/wechat/imports','POST',request)[0],404)

    def test_record_progress_is_persisted_before_the_next_record_finishes(self):
        client=self.client('wechat-record-progress@test.local')
        request=json.loads((Path(__file__).parent.parent/'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        record=copy.deepcopy(request['records'][0])
        record.update(text='课程介绍材料。',text_quality='complete',attachments=[],context=[])
        request.update(request_id='record-progress',records=[dict(record,input_id='progress-1'),dict(record,input_id='progress-2')])
        started,release=threading.Event(),threading.Event()
        calls=[]
        def adapter(body,loader,model,settings,limits):
            rid=body['records'][0]['input_id'];calls.append(rid)
            self.assertLessEqual(limits.batch_seconds,180)
            self.assertLessEqual(limits.record_seconds,70)
            if rid=='progress-2':
                started.set();release.wait(5)
            return dict(contract_version='0.3.1',request_id=body['request_id'],status='no_tasks',tasks=[],errors=[],
                record_results=[dict(input_id=rid,status='no_tasks',task_ids=[],warnings=[],coverage=dict(complete=True,uncertain=False),attachments=[])])
        options=dict(provider='qiniu',key='synthetic-key',model='test-image-model',endpoint='https://example.invalid/v1/chat/completions')
        with patch('ddl_text.codex_client.live_options',return_value=options),patch('ddl_text.media_pipeline.process_media_request',side_effect=adapter):
            _,ack=self.call(client,'/sources/wechat/imports','POST',request)
            try:
                self.assertTrue(started.wait(3))
                _,running=self.call(client,'/sources/wechat/imports/'+ack['job_id'])
                self.assertEqual(running['status'],'running')
                self.assertEqual(running['progress'],dict(total_records=2,finished_records=1,failed_records=0))
                with app.connection() as db:
                    saved=db.execute('SELECT result FROM wechat_record_runs WHERE job_id=? AND input_id=?',(ack['job_id'],'progress-1')).fetchone()
                    self.assertEqual(json.loads(saved['result'])['status'],'no_tasks')
                self.assertIsNone(running['result'])
            finally:
                release.set()
            for _ in range(200):
                _,job=self.call(client,'/sources/wechat/imports/'+ack['job_id'])
                if job['status'] not in ('queued','running'):break
                time.sleep(.02)
        self.assertEqual(job['status'],'succeeded')
        self.assertEqual(job['progress']['finished_records'],2)
        self.assertEqual(calls,['progress-1','progress-2'])
        check_pair(request,job['result'])

    def test_restart_preserves_completed_records_and_does_not_repeat_an_inflight_call(self):
        client=self.client('wechat-checkpoint-recovery@test.local')
        request=json.loads((Path(__file__).parent.parent/'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        record=copy.deepcopy(request['records'][0]);record.update(text='课程说明。',text_quality='complete',attachments=[],context=[])
        request.update(request_id='checkpoint-recovery',records=[dict(record,input_id='saved-1'),dict(record,input_id='inflight-2'),dict(record,input_id='unstarted-3')])
        def result(body):
            rid=body['records'][0]['input_id']
            return dict(contract_version='0.3.1',request_id=body['request_id'],status='no_tasks',tasks=[],errors=[],
                record_results=[dict(input_id=rid,status='no_tasks',task_ids=[],warnings=[],coverage=dict(complete=True,uncertain=False),attachments=[])])
        with patch.object(wechat_media,'_process'):
            _,ack=self.call(client,'/sources/wechat/imports','POST',request)
        saved=result(dict(request,records=[request['records'][0]]))
        child = '''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()/'server'))
import wechat_media
def adapter(body,*args):
    rid=body['records'][0]['input_id']
    if rid=='inflight-2': os._exit(23)
    return dict(contract_version='0.3.1',request_id=body['request_id'],status='no_tasks',tasks=[],errors=[],
        record_results=[dict(input_id=rid,status='no_tasks',task_ids=[],warnings=[],coverage=dict(complete=True,uncertain=False),attachments=[])])
options=dict(provider='qiniu',key='synthetic-key',model='test-image-model',endpoint='https://example.invalid/v1/chat/completions')
with patch('ddl_text.codex_client.live_options',return_value=options),patch('ddl_text.media_pipeline.process_media_request',side_effect=adapter):
    wechat_media._process(Path(sys.argv[1]),sys.argv[2])
'''
        crashed=subprocess.run([sys.executable,'-c',child,str(app.DB),ack['job_id']],cwd=Path(__file__).parent.parent,
            timeout=15,capture_output=True)
        self.assertEqual(crashed.returncode,23,crashed.stderr.decode('utf-8',errors='replace'))
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT status FROM wechat_jobs WHERE id=?',(ack['job_id'],)).fetchone()['status'],'running')
            markers={row['input_id']:row['result'] for row in db.execute('SELECT input_id,result FROM wechat_record_runs WHERE job_id=?',(ack['job_id'],))}
            self.assertEqual(json.loads(markers['saved-1']),saved)
            self.assertIsNone(markers['inflight-2'])
            wechat_media.initialize(db)
        options=dict(provider='qiniu',key='synthetic-key',model='test-image-model',endpoint='https://example.invalid/v1/chat/completions')
        with patch('ddl_text.codex_client.live_options',return_value=options),patch('ddl_text.media_pipeline.process_media_request',side_effect=lambda body,*args:result(body)) as adapter:
            wechat_media._process(app.DB,ack['job_id'])
            self.assertEqual([call.args[0]['records'][0]['input_id'] for call in adapter.call_args_list],['unstarted-3'])
            wechat_media._process(app.DB,ack['job_id'])
            self.assertEqual(adapter.call_count,1)
            _,job=self.call(client,'/sources/wechat/imports/'+ack['job_id'])
            self.assertEqual(job['status'],'partial_failed')
            self.assertEqual(job['progress'],dict(total_records=3,finished_records=3,failed_records=1))
            self.assertEqual(job['result']['record_results'][0],saved['record_results'][0])
            self.assertEqual(wechat_media.retryable_records(job['result']),{'inflight-2'})
            check_pair(request,job['result'])
            status,_=self.call(client,'/sources/wechat/imports/'+ack['job_id']+'/retry','POST',dict(retry_id='retry-interrupted-only'))
            self.assertEqual(status,202)
            for _ in range(200):
                _,job=self.call(client,'/sources/wechat/imports/'+ack['job_id'])
                if job['status'] not in ('queued','running'):break
                time.sleep(.02)
            self.assertEqual(job['status'],'succeeded')
            self.assertEqual([call.args[0]['records'][0]['input_id'] for call in adapter.call_args_list],['unstarted-3','inflight-2'])

    def test_interrupted_file_results_pass_the_formal_contract(self):
        request=json.loads((Path(__file__).parent.parent/'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        interrupted=wechat_media._interrupted_record(request,request['records'][0])
        check_pair(request,interrupted)
        self.assertEqual(interrupted['status'],'failed')
        self.assertEqual(interrupted['record_results'][0]['attachments'][0]['coverage_basis'],'unverified')
        self.assertEqual(wechat_media.retryable_records(interrupted),{request['records'][0]['input_id']})

    def test_all_saved_record_results_recover_without_model_configuration(self):
        request=json.loads((Path(__file__).parent.parent/'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        result=json.loads((Path(__file__).parent.parent/'contracts/text-to-app/text-attachments-v031-output.json').read_text(encoding='utf-8'))
        completed={request['records'][0]['input_id']:result}
        with patch('ddl_text.codex_client.live_options',side_effect=ValueError('not configured')) as options:
            recovered=wechat_media.recognize_v03(request,lambda *args,**kwargs:None,checkpoint=lambda *args:None,completed=completed)
        self.assertEqual(recovered,result)
        options.assert_not_called()

    def test_revoked_file_cannot_be_retried_after_a_recoverable_model_failure(self):
        try:
            from ddl_text.tests.media_cases import FakeMediaClient,image_bytes,request_for
        except ImportError:
            self.skipTest('image runtime dependencies unavailable')
        client=self.client('wechat-revoked-retry@test.local')
        pixels=image_bytes();request,_=request_for(pixels)
        request['request_id']='revoked-file-retry'
        _,receipt=self.call(client,'/sources/wechat/assets','POST',pixels,multipart=True)
        request['records'][0]['attachments'][0].update({key:receipt[key] for key in ('file_ref','file_name','mime_type','size_bytes','sha256')})
        options=dict(provider='qiniu',key='synthetic-key',model='test-image-model',endpoint='https://example.invalid/v1/chat/completions')
        with patch('ddl_text.codex_client.live_options',return_value=options),patch('ddl_text.chat_client.ChatClient',return_value=FakeMediaClient(behavior='failure')):
            _,ack=self.call(client,'/sources/wechat/imports','POST',request)
            for _ in range(200):
                _,job=self.call(client,'/sources/wechat/imports/'+ack['job_id'])
                if job['status'] not in ('queued','running'):break
                time.sleep(.02)
        self.assertEqual(job['status'],'failed')
        self.assertTrue(wechat_media.retryable_records(job['result']))
        self.assertEqual(self.call(client,'/sources/wechat/assets/'+receipt['file_ref'],'DELETE')[0],204)
        self.assertEqual(self.call(client,'/sources/wechat/imports/'+ack['job_id']+'/retry','POST',dict(retry_id='after-revocation'))[0],404)


if __name__ == '__main__': unittest.main()
