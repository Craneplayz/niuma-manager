"""Real HTTP acceptance/recovery with synthetic, controllable model completions."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from jsonschema import Draft202012Validator, FormatChecker
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import app
import recognition
import text_jobs

FIXTURE = json.loads((Path(__file__).resolve().parents[1]/'contracts/text-to-app/text-contract-v0.2.json').read_text())
CONTRACTS = Path(__file__).resolve().parents[1]/'contracts'
SCHEMA = Draft202012Validator(json.loads((CONTRACTS/'schemas/text-import-jobs-v0.1.schema.json').read_text()),format_checker=FormatChecker())


class TextJobsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.folder.name)/'text.sqlite3'
        app.initialize()
        with app.connection() as db:
            for uid in ('A','B'):
                db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,uid+'@text.test',uid,'00'*16,'unused'))
                db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(uid.encode()).hexdigest(),uid,time.time()+3600))
        self.server = app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:%d/api' % self.server.server_port
        self.request = copy.deepcopy(FIXTURE['request'])
        self.request['request_id'] = 'synthetic:text'
        self.request['records'] = [self.request['records'][0]]
        self.request['records'][0]['platform'] = 'manual'
        self.release = threading.Event()
        self.started = threading.Event()
        self.calls = 0

    def tearDown(self):
        self.release.set()
        text_jobs.wait_for_jobs(app.DB)
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        app.DB = self.original_db
        self.folder.cleanup()

    def call(self, path='/recognition/text-jobs', body=None, uid='A'):
        request = Request(self.base+path, None if body is None else json.dumps(body).encode(),
                          {'Content-Type':'application/json','Cookie':app.session_cookie(uid,3600).split(';')[0]})
        try: response = urlopen(request,timeout=5)
        except HTTPError as error: response = error
        with response:
            value = json.load(response)
            if response.status == 200 and path.startswith('/recognition/text-jobs'): SCHEMA.validate(value)
            return response.status,value

    def test_shared_contract_examples(self):
        for envelope in json.loads((CONTRACTS/'examples/text-import-jobs-v01.json').read_text()):
            SCHEMA.validate(envelope)

    def outcome(self, request, *, task=False, failed=False):
        rid = request['records'][0]['input_id']
        tasks = [copy.deepcopy(FIXTURE['expected']['tasks'][0])] if task else []
        if tasks: tasks[0]['sources'][0]['platform'] = 'manual'
        status = 'failed' if failed else 'success' if task else 'no_tasks'
        return dict(contract_version='0.2', request_id=request['request_id'],status=status,
                    record_results=[dict(input_id=rid,status=status,task_ids=[t['candidate_id'] for t in tasks],warnings=[])],
                    tasks=tasks,errors=[dict(input_id=rid,code='invalid_agent_output',message='合成格式失败',retryable=True)] if failed else [])

    def recognize(self, request, ApiError, *, on_admitted=None):
        self.calls += 1
        if on_admitted: on_admitted()
        self.started.set()
        if not self.release.wait(10): raise RuntimeError('synthetic worker was not released')
        return self.outcome(request,task=True)

    def job(self):
        return self.call()[1]['jobs'][0]

    def action(self, action, attempt=1, uid='A'):
        return self.call('/recognition/text-jobs/'+quote(self.request['request_id'],safe='')+'/'+action,
                         dict(attempt=attempt),uid)

    def test_acceptance_reload_duplicate_and_candidate_persistence(self):
        with patch.object(recognition,'recognize',self.recognize):
            status,envelope = self.call(body=self.request)
            self.assertEqual(status,200)
            self.assertTrue(self.started.wait(3))
            self.assertEqual(self.job()['state'],'recognizing')
            self.assertNotIn('request',envelope['jobs'][0])
            self.assertEqual(self.call('/recognition/batches')[1],[])
            self.assertEqual(self.call(body=self.request)[0],200)
            self.assertEqual(self.calls,1)
            changed = copy.deepcopy(self.request); changed['records'][0]['text'] += '改变'
            self.assertEqual(self.call(body=changed)[0],409)
            self.release.set(); text_jobs.wait_for_jobs(app.DB)
        self.assertEqual(self.job()['state'],'success')
        batches = self.call('/recognition/batches')[1]
        self.assertEqual(len(batches),1)
        self.assertEqual(len(batches[0]['items']),1)
        self.assertEqual(batches[0]['items'][0]['state'],'pending')
        self.assertEqual(self.call(body=self.request)[0],200)
        text_jobs.wait_for_jobs(app.DB)
        self.assertEqual(self.calls,1)

    def test_stop_rejects_late_results_and_stale_retry_is_idempotent(self):
        with patch.object(recognition,'recognize',self.recognize):
            self.call(body=self.request); self.assertTrue(self.started.wait(3))
            self.assertEqual(self.action('stop')[0],200)
            self.assertEqual(self.job()['state'],'stopped')
            self.release.set(); text_jobs.wait_for_jobs(app.DB)
            self.assertEqual(self.call('/recognition/batches')[1],[])
            self.assertEqual(self.action('retry')[0],200)
            text_jobs.wait_for_jobs(app.DB)
            self.assertEqual(self.job()['attempt'],2)
            self.assertEqual(self.job()['state'],'success')
            self.action('retry',1); self.action('stop',1)
            text_jobs.wait_for_jobs(app.DB)
        self.assertEqual(self.calls,2)
        self.assertEqual(self.job()['state'],'success')
        self.assertEqual(len(self.call('/recognition/batches')[1][0]['items']),1)

    def test_empty_and_failure_remain_in_history_and_legacy_sync_cannot_bypass_stop(self):
        with patch.object(recognition,'recognize',lambda req,err,**kw:self.outcome(req,failed=True)):
            self.call(body=self.request); text_jobs.wait_for_jobs(app.DB)
        self.assertEqual(self.job()['state'],'failed')
        with patch.object(recognition,'recognize',lambda req,err,**kw:self.outcome(req)):
            self.action('retry'); text_jobs.wait_for_jobs(app.DB)
        self.assertEqual(self.job()['state'],'no_tasks')
        self.assertEqual(self.call('/recognition/batches')[1][0]['result']['status'],'no_tasks')
        self.assertEqual(self.call('/recognition/retry',self.request)[0],409)

    def test_retry_configuration_failure_updates_job_but_preserves_old_batch(self):
        with patch.object(recognition,'recognize',lambda req,err,**kw:self.outcome(req,failed=True)):
            self.call(body=self.request); text_jobs.wait_for_jobs(app.DB)
        def unavailable(request,ApiError,**kwargs):
            error=ApiError(503,'文本识别配置不可用，请检查当前环境的模型配置文件')
            error.code='model_configuration'
            raise error
        with patch.object(recognition,'recognize',unavailable):
            self.action('retry'); text_jobs.wait_for_jobs(app.DB)
        job=self.job()
        self.assertEqual(job['attempt'],2)
        self.assertEqual(job['error']['code'],'model_configuration')
        old=self.call('/recognition/batches')[1][0]
        self.assertEqual(old['result']['errors'][0]['code'],'invalid_agent_output')

    def test_legacy_job_rows_gain_a_stable_internal_worker_identity(self):
        with app.connection() as db:
            db.execute('DROP TABLE text_import_jobs')
            db.execute('''CREATE TABLE text_import_jobs(user_id TEXT,request_id TEXT,fingerprint TEXT,
                request TEXT,state TEXT,attempt INTEGER,error TEXT,created_at TEXT,updated_at TEXT,
                PRIMARY KEY(user_id,request_id))''')
            db.execute('INSERT INTO text_import_jobs VALUES(?,?,?,?,?,?,?,?,?)',
                       ('A','old','fingerprint','{}','no_tasks',1,None,text_jobs.stamp(),text_jobs.stamp()))
            text_jobs.initialize(db,recover=False)
            identity=db.execute('SELECT instance_id FROM text_import_jobs').fetchone()[0]
            self.assertEqual(len(identity),32)
            text_jobs.initialize(db,recover=False)
            self.assertEqual(db.execute('SELECT instance_id FROM text_import_jobs').fetchone()[0],identity)

    def test_account_isolation_and_restart_marks_interrupted_without_reissuing(self):
        with patch.object(recognition,'recognize',self.recognize):
            self.call(body=self.request); self.assertTrue(self.started.wait(3))
            self.assertEqual(self.call(uid='B')[1]['jobs'],[])
            self.assertEqual(self.action('stop',uid='B')[0],404)
            with app.connection() as db: text_jobs.initialize(db,recover=True)
            self.assertEqual(self.job()['state'],'interrupted')
            self.release.set(); text_jobs.wait_for_jobs(app.DB)
            self.assertEqual(self.call('/recognition/batches')[1],[])
        self.assertEqual(self.calls,1)

    def test_one_active_per_account_and_stop_while_waiting_never_calls_model(self):
        # Exercise the actual shared model admission rather than an invented UI state.
        from server.model_queue import ModelQueue
        queue = ModelQueue(limit=1,wait=3)
        queue.acquire()
        with patch.object(recognition,'_ACTIVE',queue):
            self.call(body=self.request)
            self.assertEqual(self.job()['state'],'queued')
            other = copy.deepcopy(self.request); other['request_id'] = 'another'
            self.assertEqual(self.call(body=other)[0],409)
            self.action('stop'); text_jobs.wait_for_jobs(app.DB)
        queue.release()
        self.assertEqual(self.job()['state'],'stopped')
        self.assertEqual(self.call('/recognition/batches')[1],[])

    def test_receipt_disconnect_does_not_prevent_accepted_work(self):
        class ClosedReceipt:
            command = 'POST'
            def reply(self,*args): raise BrokenPipeError()
        with patch.object(recognition,'recognize',self.recognize):
            with app.connection() as db:
                with self.assertRaises(BrokenPipeError):
                    text_jobs.route(ClosedReceipt(),db,'A','/api/recognition/text-jobs',self.request,app.ApiError,app.DB)
            self.assertTrue(self.started.wait(3))
            self.release.set(); text_jobs.wait_for_jobs(app.DB)
        self.assertEqual(self.job()['state'],'success')


if __name__ == '__main__': unittest.main()
