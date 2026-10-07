import copy
import json
import tempfile
import time
import threading
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

    def call(self, client, path, method='GET', body=None, token=None, multipart=False):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        if multipart:
            boundary = 'niuma-test-boundary'
            data = b'--' + boundary.encode() + b'\r\nContent-Disposition: form-data; name="file"; filename="work.png"\r\nContent-Type: image/png\r\n\r\n' + body + b'\r\n--' + boundary.encode() + b'--\r\n'
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        else:
            data = json.dumps(body).encode() if body is not None else None
            if data is not None: headers['Content-Type'] = 'application/json'
        try:
            response = client.open(Request(self.base + path, data, headers, method=method))
        except HTTPError as error:
            response = error
        return response.status, json.loads(response.read())

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
        with patch.object(wechat_media, 'recognize_v03', side_effect=lambda body,assets:wechat_media._failed_result(body)) as model:
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


if __name__ == '__main__': unittest.main()
