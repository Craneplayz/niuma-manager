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

    def test_quick_add_persists_once_without_user_review(self):
        self.request['request_id'] = 'quick-add:synthetic'
        with patch.object(recognition, 'recognize', self.recognize):
            self.release.set()
            self.assertEqual(self.call(body=self.request)[0], 200)
            text_jobs.wait_for_jobs(app.DB)
            self.assertEqual(self.job()['state'], 'success')
            self.assertEqual(self.call(body=self.request)[0], 200)
        self.assertEqual(self.calls, 1)
        task, = self.call('/tasks/quick-recognition')[1]['value']
        self.assertEqual(task['source'], '一句话添加')
        self.assertEqual(task['recognitionHistory'][0]['requestId'], self.request['request_id'])
        self.assertEqual(self.call('/recognition/batches')[1][0]['items'][0]['state'], 'applied')
        self.assertEqual(self.call('/tasks/quick-recognition', uid='B')[1]['value'], [])

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
        current_id=job['error']['diagnostic_id']
        previous_id=old['result']['errors'][0]['diagnostic_id']
        self.assertNotEqual(current_id,previous_id)
        self.assertEqual(job['error']['failure']['reason'],'model_configuration')
        self.assertEqual(json.loads(job['error']['diagnostics'])['attempt_id'],current_id)
        self.assertEqual(json.loads(old['result']['errors'][0]['diagnostics'])['attempt_id'],previous_id)
        lookup=self.call('/recognition/diagnostics',dict(diagnostic_id=current_id))[1]
        self.assertEqual(lookup['attempts'][0]['attempt_id'],current_id)
        self.assertEqual(self.call('/recognition/diagnostics',dict(diagnostic_id=current_id),uid='B')[1]['attempts'],[])
        with patch('server.recognition_diagnostics.detailed',return_value=False):
            public=self.job()['error']
        self.assertEqual(public['diagnostic_id'],current_id)
        self.assertNotIn('diagnostics',public)

    def test_failed_candidate_save_keeps_safe_diagnostic_without_partial_candidates(self):
        with app.connection() as db:
            db.execute("""CREATE TRIGGER synthetic_save_failure BEFORE INSERT ON recognition_candidates
                BEGIN SELECT RAISE(FAIL,'private synthetic storage error'); END""")
        with patch.object(recognition,'recognize',lambda req,err,**kw:self.outcome(req,task=True)):
            self.call(body=self.request); text_jobs.wait_for_jobs(app.DB)
        error=self.job()['error']
        self.assertEqual(error['failure']['reason'],'save_failed')
        self.assertNotIn('private synthetic',json.dumps(error))
        self.assertEqual(self.call('/recognition/batches')[1],[])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recognition_candidates').fetchone()[0],0)
        lookup=self.call('/recognition/diagnostics',dict(diagnostic_id=error['diagnostic_id']))[1]
        self.assertEqual(len(lookup['attempts']),1)

    def test_safe_model_validation_detail_survives_job_storage_and_schema(self):
        from contracts.recognition_errors import describe
        def invalid(request,ApiError,**kwargs):
            result=self.outcome(request,failed=True)
            result['errors']=[describe(result['errors'][0],reason='result_contract',
                detail=dict(schema_path='properties/tasks/items/required',rule='required',missing_fields=['title']))]
            return result
        with patch.object(recognition,'recognize',invalid):
            self.call(body=self.request); text_jobs.wait_for_jobs(app.DB)
        error=self.job()['error']
        self.assertEqual(error['diagnostic']['missing_fields'],['title'])
        self.assertEqual(error['failure']['reason'],'result_contract')
        self.assertEqual(len(error['diagnostic_id']),24)

    def test_legacy_job_rows_gain_a_stable_internal_worker_identity(self):
        with app.connection() as db:
            db.execute('DROP TABLE text_import_jobs')
            db.execute('''CREATE TABLE text_import_jobs(user_id TEXT,request_id TEXT,fingerprint TEXT,
                request TEXT,state TEXT,attempt INTEGER,error TEXT,created_at TEXT,updated_at TEXT,
                PRIMARY KEY(user_id,request_id))''')
            db.execute('INSERT INTO text_import_jobs VALUES(?,?,?,?,?,?,?,?,?)',
                       ('A','old','fingerprint','{}','no_tasks',1,None,text_jobs.stamp(),text_jobs.stamp()))
            db.execute("CREATE UNIQUE INDEX text_import_one_active ON text_import_jobs(user_id) WHERE state IN ('queued','recognizing')")
            text_jobs.initialize(db,recover=False)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='text_import_one_active'").fetchone())
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

    def test_multiple_waiting_jobs_can_be_stopped_independently(self):
        # Exercise the actual shared model admission rather than an invented UI state.
        from server.model_queue import ModelQueue
        queue = ModelQueue(limit=1,wait=3)
        queue.acquire()
        with patch.object(recognition,'_ACTIVE',queue):
            self.call(body=self.request)
            self.assertEqual(self.job()['state'],'queued')
            other = copy.deepcopy(self.request); other['request_id'] = 'another'
            self.assertEqual(self.call(body=other)[0],200)
            self.action('stop')
            self.assertEqual({j['request_id']: j['state'] for j in self.call()[1]['jobs']},
                             {self.request['request_id']: 'stopped', 'another': 'queued'})
            self.assertEqual(self.call('/recognition/text-jobs/another/stop', {'attempt': 1})[0], 200)
            text_jobs.wait_for_jobs(app.DB)
        queue.release()
        self.assertTrue(all(j['state'] == 'stopped' for j in self.call()[1]['jobs']))
        self.assertEqual(self.call('/recognition/batches')[1],[])

    def test_full_windows_accept_fifo_backlog_and_publish_independent_results(self):
        from queue import Queue
        claimed = Queue()
        ids = [f'notice-{i}' for i in range(23)]
        gates = {rid: threading.Event() for rid in ids}

        def recognize(request, error, *, on_admitted):
            rid = request['request_id']
            on_admitted()
            claimed.put(rid)
            if not gates[rid].wait(30):
                raise AssertionError('test did not release worker')
            return self.outcome(request)

        def submit(index, uid):
            status, result = self.call(body=dict(self.request, request_id=ids[index]), uid=uid)
            self.assertEqual(status, 200)
            return next(job['state'] for job in result['jobs'] if job['request_id'] == ids[index])

        with patch.object(recognition, 'recognize', recognize):
            try:
                for i in range(20):
                    submit(i, 'A')
                self.assertEqual({claimed.get(timeout=5) for _ in range(20)}, set(ids[:20]))
                # A fills the windows; B can submit, replay and cancel/retry while all are busy.
                self.assertEqual(submit(20, 'B'), 'queued')
                self.assertEqual(submit(20, 'B'), 'queued')
                self.assertEqual(submit(21, 'A'), 'queued')
                self.assertEqual(submit(22, 'B'), 'queued')
                path = '/recognition/text-jobs/' + ids[20]
                self.assertEqual(self.call(path, uid='A')[0], 404)
                for action in ('stop', 'retry'):
                    self.assertEqual(self.call(path+'/'+action, {'attempt': 1}, uid='B')[0], 200)
                # One free window drains the waiting jobs, even while the first notice is blocked.
                gates[ids[19]].set()
                for i in (21, 22, 20):
                    self.assertEqual(claimed.get(timeout=5), ids[i])
                    gates[ids[i]].set()
                self.assertEqual(self.call('/recognition/text-jobs/'+ids[0])[1]['jobs'][0]['state'], 'recognizing')
            finally:
                for gate in gates.values():
                    gate.set()
                text_jobs.wait_for_jobs(app.DB)
        self.assertTrue(claimed.empty())
        for uid in ('A', 'B'):
            self.assertTrue(all(job['state'] == 'no_tasks' for job in self.call(uid=uid)[1]['jobs']))

    def test_startup_resumes_queued_receipts_and_detail_outlives_history(self):
        with app.connection() as db:
            for i in range(101):
                text_jobs.create(db, 'A', dict(self.request, request_id=f'backlog-{i}'), app.ApiError)
                db.commit()
        oldest = '/recognition/text-jobs/backlog-0'
        self.assertEqual(len(self.call()[1]['jobs']), 100)
        self.assertEqual(self.call(oldest)[1]['jobs'][0]['state'], 'queued')
        with patch.object(recognition, 'recognize', lambda req, err, **kw: self.outcome(req)), \
                patch('app.runtime_environment.allow_background_recovery', return_value=True):
            app.initialize()
            text_jobs.wait_for_jobs(app.DB)
        self.assertEqual(self.call(oldest)[1]['jobs'][0]['state'], 'no_tasks')

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
