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
from contracts.check_v03_examples import check_pair


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
        request = json.loads((Path(__file__).parent.parent / 'contracts/source-to-text/text-attachments-v03-request.json').read_text(encoding='utf-8'))
        asset = request['records'][0]['attachments'][0]
        asset.update({key:ack[key] for key in ('file_ref','file_name','mime_type','size_bytes','sha256')})
        self.assertEqual(self.call(b, '/sources/wechat/imports', 'POST', request)[0], 404)
        status, submitted = self.call(a, '/sources/wechat/imports', 'POST', request)
        self.assertEqual(status, 202)
        self.assertEqual(self.call(a, '/sources/wechat/imports', 'POST', request)[0], 200)
        same = copy.deepcopy(request); same['request_id'] = 'another-collection'; same['records'][0]['collected_at'] = '2026-10-02T20:05:00+08:00'
        self.assertEqual(self.call(a, '/sources/wechat/imports', 'POST', same)[1]['job_id'], submitted['job_id'])
        changed = copy.deepcopy(request); changed['records'][0]['source_label'] = 'different'
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
        self.assertEqual(status, 202)
        self.assertEqual(self.call(a, '/sources/wechat/imports/' + submitted['job_id'] + '/retry', 'POST', dict(retry_id='retry-once'))[0], 202)
        for _ in range(100):
            _, job = self.call(a, '/sources/wechat/imports/' + submitted['job_id'])
            if job['status'] == 'failed': break
            time.sleep(.02)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(self.call(a, '/sources/wechat/imports/' + submitted['job_id'] + '/retry', 'POST', dict(retry_id='retry-twice'))[0], 202)
        self.assertEqual(self.call(a, '/sources/wechat/credential/revoke', 'POST', {})[0], 200)
        self.assertEqual(self.call(a, '/sources/wechat/imports/' + submitted['job_id'], token=token)[0], 401)

    def test_valid_adapter_result_auto_adds_once_and_stays_owned(self):
        a, b = self.client('wechat-success@test.local'), self.client('wechat-success-other@test.local')
        raw = b'\x89PNG\r\n\x1a\nnew-image'
        _, ack = self.call(a, '/sources/wechat/assets', 'POST', raw, multipart=True)
        request = json.loads((Path(__file__).parent.parent / 'contracts/source-to-text/text-attachments-v03-request.json').read_text(encoding='utf-8'))
        request['request_id'] = 'wechat-success-test'
        asset = request['records'][0]['attachments'][0]
        asset.update({key:ack[key] for key in ('file_ref','file_name','mime_type','size_bytes','sha256')})
        result = json.loads((Path(__file__).parent.parent / 'contracts/text-to-app/text-attachments-v03-output.json').read_text(encoding='utf-8'))
        result['request_id'] = request['request_id']
        with patch.object(wechat_media, 'recognize_v03', return_value=result):
            status, submitted = self.call(a, '/sources/wechat/imports', 'POST', request)
            self.assertEqual(status, 202)
            for _ in range(100):
                _, job = self.call(a, '/sources/wechat/imports/' + submitted['job_id'])
                if job['status'] == 'succeeded': break
                time.sleep(.02)
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(len(self.call(a, '/workspace')[1]['tasks']['value']), 1)
        self.assertEqual(len(self.call(b, '/workspace')[1].get('tasks', {}).get('value', [])), 0)
        self.assertEqual(self.call(a, '/sources/wechat/imports', 'POST', request)[0], 200)
        self.assertEqual(len(self.call(a, '/workspace')[1]['tasks']['value']), 1)
        self.assertEqual(len(self.call(a, '/sources/wechat/imports')[1]['jobs']), 1)

    def test_restart_recovers_a_running_job_without_losing_it(self):
        a = self.client('wechat-recover@test.local')
        request = json.loads((Path(__file__).parent.parent / 'contracts/source-to-text/text-attachments-v03-request.json').read_text(encoding='utf-8'))
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
        cases = json.loads((Path(__file__).parent.parent / 'contracts/examples/text-attachments-v03-cases.json').read_text(encoding='utf-8'))
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
