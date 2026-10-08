"""Backend recognition: direct spawn workers, HTTP compatibility and busy gate."""
import copy
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from unittest.mock import patch

import app
import recognition
from ddl_text.codex_client import ENV_KEYS
from ddl_text.extraction import OfflineDraftClient

ROOT = Path(__file__).resolve().parent.parent
REQUEST = json.loads((ROOT / 'contracts/source-to-text/text-frontend-v02-request.json').read_text(encoding='utf-8'))
DRAFTS = json.loads((ROOT / 'ddl_text/tests/fixtures/text-frontend-v02-drafts.json').read_text(encoding='utf-8'))


class RecognitionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.original_db = app.DB
        app.DB = Path(cls.folder.name) / 'runtime.sqlite3'
        app.initialize()
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%d/api' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        app.DB = cls.original_db
        cls.folder.cleanup()

    def setUp(self):
        # Neither a developer's .env nor exported credentials may reach test providers.
        self.env = patch.dict(recognition.os.environ, {**dict.fromkeys(ENV_KEYS, ''),
            'TEXT_HARNESS_URL': '', 'TEXT_HARNESS_TOKEN': '', 'DDL_PROVIDER': 'qiniu'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.env_folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.env_folder.cleanup)
        self.root = patch.object(recognition, 'ROOT', Path(self.env_folder.name))
        self.root.start()
        self.addCleanup(self.root.stop)
        self.request = copy.deepcopy(REQUEST)
        self.request['request_id'] = self.id()
        self.client = self.new_client(self.id())

    def call(self, client, path, data=None):
        try:
            response = client.open(Request(self.base + path,
                None if data is None else json.dumps(data).encode(),
                {'Content-Type': 'application/json'}), timeout=20)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def new_client(self, name):
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        status, _ = self.call(client, '/auth/register', dict(
            name='Synthetic', email=name + '@runtime.test', password='Synthetic-only-2026!'))
        self.assertEqual(status, 200)
        return client

    def submit(self, request=None, client=None, retry=False):
        return self.call(client or self.client,
            '/recognition/retry' if retry else '/recognition/batches',
            request if request is not None else self.request)

    def empty_result(self, request):
        return dict(contract_version='0.2', request_id=request['request_id'], status='no_tasks',
            record_results=[dict(input_id=r['input_id'], status='no_tasks', task_ids=[], warnings=[])
                            for r in request['records']], tasks=[], errors=[])

    def policy_rows(self,client=None):
        uid=self.call(client or self.client,'/auth/me')[1]['user']['id']
        with app.connection() as db:
            return {row['input_id']:json.loads(row['policy']) if row['policy'] is not None else None
                    for row in db.execute('SELECT input_id,policy FROM recognition_record_policies WHERE user_id=? AND request_id=?',
                        (uid,self.request['request_id']))}

    def test_record_policy_uses_actual_settings_and_excludes_credentials(self):
        versions=[]
        with patch.object(recognition,'make_client',return_value=OfflineDraftClient(DRAFTS)), \
                patch.object(recognition,'process_request',side_effect=lambda req,*args,**kwargs:self.empty_result(req)):
            for key,model,endpoint in [('synthetic-key-one','policy-model','https://synthetic.invalid/v1'),
                                       ('synthetic-key-two','policy-model','https://synthetic.invalid/v1'),
                                       ('synthetic-key-two','new-policy-model','https://synthetic.invalid/v1'),
                                       ('synthetic-key-two','new-policy-model','https://other-synthetic.invalid/v1')]:
                self.request['request_id']+=':next'
                with patch.dict(recognition.os.environ,{'QINIU_API_KEY':key,'DDL_MODEL':model,'DDL_ENDPOINT':endpoint}):
                    self.assertEqual(self.submit()[0],200)
                policies=self.policy_rows()
                self.assertEqual(set(policies),{record['input_id'] for record in self.request['records']})
                policy=next(iter(policies.values()))
                self.assertTrue(all(item==policy for item in policies.values()))
                self.assertEqual(policy['execution'],dict(provider='qiniu',model=model,endpoint=endpoint,
                    timeout_seconds=90.0,max_retries=0,deployment_revision=None))
                self.assertEqual(policy['components'],recognition._POLICY_COMPONENTS)
                self.assertNotIn(key,json.dumps(policy))
                versions.append(policy['version'])
        self.assertEqual(versions[0],versions[1])
        self.assertNotEqual(versions[1],versions[2])
        self.assertNotEqual(versions[2],versions[3])

    def test_deepseek_transport_policy_is_codex_not_qiniu(self):
        with patch.dict(recognition.os.environ,{'DDL_PROVIDER':'deepseek','DEEPSEEK_API_KEY':'synthetic-codex-key'}), \
                patch.object(recognition,'make_client',return_value=OfflineDraftClient(DRAFTS)), \
                patch.object(recognition,'process_request',side_effect=lambda req,*args,**kwargs:self.empty_result(req)):
            self.assertEqual(self.submit()[0],200)
        policies=self.policy_rows()
        self.assertTrue(all(policy['execution']['provider']=='codex' for policy in policies.values()))

    def test_completed_replay_keeps_executed_policy_without_another_call(self):
        with patch.dict(recognition.os.environ,{'QINIU_API_KEY':'synthetic-only','DDL_MODEL':'original-model'}), \
                patch.object(recognition,'make_client',return_value=OfflineDraftClient(DRAFTS)), \
                patch.object(recognition,'process_request',side_effect=lambda req,*args,**kwargs:self.empty_result(req)) as pipeline:
            self.assertEqual(self.submit()[0],200)
            original=self.policy_rows()
            with patch.dict(recognition.os.environ,{'DDL_MODEL':'replacement-model'}):
                self.assertEqual(self.submit()[0],200)
                self.assertEqual(self.submit(retry=True)[0],200)
            self.assertEqual(pipeline.call_count,1)
        self.assertEqual(self.policy_rows(),original)

    def test_legacy_results_are_not_backfilled_and_survive_initialization(self):
        with patch.object(recognition,'recognize',return_value=self.empty_result(self.request)):
            self.assertEqual(self.submit()[0],200)
        uid=self.call(self.client,'/auth/me')[1]['user']['id']
        with app.connection() as db:
            db.execute('DELETE FROM recognition_record_policies WHERE user_id=? AND request_id=?',
                (uid,self.request['request_id']))
        with app.connection() as db:
            recognition.initialize(db)
        with patch.object(recognition,'recognize') as pipeline:
            status,batches=self.submit()
            self.assertEqual(status,200)
            self.assertEqual(batches[0]['result'],self.empty_result(self.request))
            pipeline.assert_not_called()
        self.assertEqual(self.policy_rows(),{})

    def test_rejected_result_does_not_persist_a_policy(self):
        with patch.dict(recognition.os.environ,{'QINIU_API_KEY':'synthetic-only'}), \
                patch.object(recognition,'make_client',return_value=OfflineDraftClient(DRAFTS)), \
                patch.object(recognition,'process_request',return_value={}):
            self.assertEqual(self.submit()[0],502)
        self.assertEqual(self.policy_rows(),{})

    def test_same_record_identity_keeps_policies_separate_between_accounts(self):
        other=self.new_client(self.id()+'-other')
        with patch.dict(recognition.os.environ,{'QINIU_API_KEY':'synthetic-only','DDL_MODEL':'account-a-model'}), \
                patch.object(recognition,'make_client',return_value=OfflineDraftClient(DRAFTS)), \
                patch.object(recognition,'process_request',side_effect=lambda req,*args,**kwargs:self.empty_result(req)):
            self.assertEqual(self.submit()[0],200)
            original=self.policy_rows()
            with patch.dict(recognition.os.environ,{'DDL_MODEL':'account-b-model'}):
                self.assertEqual(self.submit(client=other)[0],200)
        self.assertEqual(self.policy_rows(),original)
        self.assertTrue(all(policy['execution']['model']=='account-b-model' for policy in self.policy_rows(other).values()))
        with app.connection() as db:
            recognition.initialize(db)
        self.assertEqual(self.policy_rows(),original)

    def test_changed_policy_files_require_restart_before_a_model_call(self):
        with patch.dict(recognition.os.environ,{'QINIU_API_KEY':'synthetic-only'}), \
                patch.object(recognition,'_POLICY_COMPONENTS',{}), \
                patch.object(recognition,'process_request') as pipeline:
            status,error=self.submit()
        self.assertEqual(status,503)
        self.assertIn('重启',error['error'])
        pipeline.assert_not_called()
        self.assertEqual(self.policy_rows(),{})

    def test_direct_pipeline_spawns_and_preserves_contract_and_idempotence(self):
        with patch.dict(recognition.os.environ, {'QINIU_API_KEY': 'synthetic-only'}), \
                patch.object(recognition, 'make_client', return_value=OfflineDraftClient(DRAFTS)) as provider, \
                patch.object(recognition, 'urlopen', side_effect=AssertionError('HTTP bridge used')):
            status, batches = self.submit()
            self.assertEqual(status, 200)
            result = batches[0]['result']
            self.assertEqual(result['status'], 'success', result['errors'])
            self.assertEqual(len(result['tasks']), 3)
            self.assertEqual(recognition.validate_result(self.request, result), result)
            self.assertFalse(batches[0]['demo'])
            self.assertEqual(self.submit(), (200, batches))
            self.assertEqual(provider.call_count, 1)
            self.assertNotIn('tasks', self.call(self.client, '/workspace')[1])
            changed = copy.deepcopy(self.request)
            changed['records'][0]['text'] += ' Different content.'
            self.assertEqual(self.submit(changed)[0], 409)

    def test_root_dotenv_and_explicit_environment_precedence(self):
        env_path = recognition.ROOT / '.env'
        env_path.write_text('DDL_PROVIDER=qiniu\nQINIU_API_KEY=synthetic-file-key\n'
                            'DDL_MODEL=synthetic-file-model\n', encoding='utf-8')
        for key in ENV_KEYS:
            recognition.os.environ.pop(key, None)
        with patch.object(recognition, 'make_client', return_value=OfflineDraftClient(DRAFTS)) as provider, \
                patch.object(recognition, 'process_request', side_effect=lambda req, *a, **kw: self.empty_result(req)) as pipeline:
            self.assertEqual(self.submit()[0], 200)
            self.assertEqual(provider.call_args.args[0]['key'], 'synthetic-file-key')
            settings = pipeline.call_args.args[2]
            self.assertEqual((settings.mode, settings.model, settings.timeout_seconds, settings.max_retries),
                             ('real', 'synthetic-file-model', 90, 0))
            self.assertEqual(pipeline.call_args.kwargs, {'budget_seconds': 90})
            recognition.os.environ['QINIU_API_KEY'] = 'synthetic-env-key'
            self.request['request_id'] += ':environment'
            self.assertEqual(self.submit()[0], 200)
            self.assertEqual(provider.call_args.args[0]['key'], 'synthetic-env-key')

    def test_http_switch_preserves_token_timeout_and_does_not_load_local_config(self):
        def harness(req, timeout):
            self.assertEqual(req.full_url, 'http://synthetic.invalid/recognize')
            self.assertEqual(req.get_header('Authorization'), 'Bearer synthetic-token')
            self.assertEqual(timeout, 95)
            self.assertEqual(json.loads(req.data), self.request)
            return io.BytesIO(json.dumps(self.empty_result(self.request)).encode())
        with patch.dict(recognition.os.environ, {'TEXT_HARNESS_URL': 'http://synthetic.invalid/recognize',
                                                'TEXT_HARNESS_TOKEN': 'synthetic-token'}), \
                patch.object(recognition, 'urlopen', side_effect=harness), \
                patch.object(recognition, 'live_options', side_effect=AssertionError('loaded local config')):
            self.assertEqual(self.submit()[0], 200)
        self.assertTrue(self.policy_rows())
        self.assertTrue(all(policy is None for policy in self.policy_rows().values()))
        with app.connection() as db:
            recognition.initialize(db)
        self.assertTrue(all(policy is None for policy in self.policy_rows().values()))

    def test_busy_gate_is_shared_between_accounts_and_transports(self):
        other = self.new_client(self.id() + '-other')
        entered, release = threading.Event(), threading.Event()
        def blocked(req, *args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError('test did not release recognition')
            return self.empty_result(req)
        with patch.dict(recognition.os.environ, {'QINIU_API_KEY': 'synthetic-only'}), \
                patch.object(recognition, 'process_request', side_effect=blocked), \
                patch.object(recognition, 'urlopen') as http, ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.submit)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.submit(client=other)[0], 503)
                self.assertEqual(self.call(other, '/recognition/batches'), (200, []))
                with patch.dict(recognition.os.environ, {'TEXT_HARNESS_URL': 'http://synthetic.invalid/recognize'}):
                    self.assertEqual(self.submit(client=other)[0], 503)
                http.assert_not_called()
                # Read-only and empty requests do not start a paid extraction.
                self.assertEqual(self.call(self.client, '/workspace')[0], 200)
                self.assertEqual(self.submit(dict(contract_version='0.2', request_id='empty', records=[]), other)[0], 200)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)[0], 200)
            self.assertEqual(self.submit(client=other)[0], 200)

    def test_configuration_and_runtime_failures_release_slot_without_saving(self):
        self.assertEqual(self.submit()[0], 503)
        recognition.ROOT.joinpath('.env').write_text('unsupported=synthetic-secret', encoding='utf-8')
        status, error = self.submit()
        self.assertEqual(status, 503)
        self.assertNotIn('synthetic-secret', json.dumps(error))
        recognition.ROOT.joinpath('.env').write_text('', encoding='utf-8')
        with patch.dict(recognition.os.environ, {'QINIU_API_KEY': 'synthetic-only'}), \
                patch.object(recognition, 'process_request', side_effect=RuntimeError('synthetic-secret')):
            status, error = self.submit()
            self.assertEqual(status, 502)
            self.assertNotIn('synthetic-secret', json.dumps(error))
        self.assertEqual(self.call(self.client, '/recognition/batches'), (200, []))
        with patch.dict(recognition.os.environ, {'QINIU_API_KEY': 'synthetic-only'}), \
                patch.object(recognition, 'process_request', side_effect=lambda req, *a, **kw: self.empty_result(req)):
            self.assertEqual(self.submit()[0], 200)

    def test_http_errors_and_invalid_result_release_slot_without_local_fallback(self):
        with patch.dict(recognition.os.environ, {'TEXT_HARNESS_URL': 'http://synthetic.invalid/recognize'}), \
                patch.object(recognition, 'process_request', side_effect=AssertionError('local fallback')):
            for failure, expected in [(HTTPError('http://synthetic.invalid', 503, 'busy', {}, io.BytesIO()), 503),
                                      (TimeoutError('synthetic-secret'), 502)]:
                with patch.object(recognition, 'urlopen', side_effect=failure):
                    status, error = self.submit()
                    self.assertEqual(status, expected)
                    self.assertNotIn('synthetic-secret', json.dumps(error))
            with patch.object(recognition, 'urlopen', return_value=io.BytesIO(b'{}')):
                self.assertEqual(self.submit()[0], 502)
            self.assertEqual(self.call(self.client, '/recognition/batches'), (200, []))
            with patch.object(recognition, 'urlopen', return_value=io.BytesIO(json.dumps(self.empty_result(self.request)).encode())):
                self.assertEqual(self.submit()[0], 200)

    def test_direct_failed_record_retry_preserves_successful_candidates(self):
        with patch.dict(recognition.os.environ, {'QINIU_API_KEY': 'synthetic-only'}), \
                patch.object(recognition, 'make_client', return_value=OfflineDraftClient(DRAFTS[:1])):
            status, batches = self.submit()
        self.assertEqual(status, 200)
        self.assertEqual(batches[0]['result']['status'], 'partial_success')
        previous_ids = {c['id'] for c in batches[0]['items']}
        previous_policies=self.policy_rows()
        with patch.dict(recognition.os.environ, {'QINIU_API_KEY': 'synthetic-only','DDL_MODEL':'updated-retry-model'}), \
                patch.object(recognition, 'make_client', return_value=OfflineDraftClient(DRAFTS)), \
                patch.object(recognition, 'process_request', wraps=recognition.process_request) as pipeline:
            status, batches = self.submit(retry=True)
            self.assertEqual(status, 200)
            self.assertEqual(batches[0]['result']['status'], 'success')
            self.assertEqual(len(batches[0]['items']), 3)
            self.assertTrue(previous_ids.issubset(c['id'] for c in batches[0]['items']))
            self.assertEqual([r['input_id'] for r in pipeline.call_args.args[0]['records']], ['synthetic:report'])
        policies=self.policy_rows()
        self.assertNotEqual(policies['synthetic:report']['version'],previous_policies['synthetic:report']['version'])
        self.assertTrue(all(policies[rid]==old for rid,old in previous_policies.items() if rid!='synthetic:report'))


if __name__ == '__main__':
    unittest.main()
