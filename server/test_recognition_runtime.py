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
        with patch.dict(recognition.os.environ, {'QINIU_API_KEY': 'synthetic-only'}), \
                patch.object(recognition, 'make_client', return_value=OfflineDraftClient(DRAFTS)), \
                patch.object(recognition, 'process_request', wraps=recognition.process_request) as pipeline:
            status, batches = self.submit(retry=True)
            self.assertEqual(status, 200)
            self.assertEqual(batches[0]['result']['status'], 'success')
            self.assertEqual(len(batches[0]['items']), 3)
            self.assertTrue(previous_ids.issubset(c['id'] for c in batches[0]['items']))
            self.assertEqual([r['input_id'] for r in pipeline.call_args.args[0]['records']], ['synthetic:report'])


if __name__ == '__main__':
    unittest.main()
