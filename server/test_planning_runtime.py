"""Planning through the real account API, with synthetic model transports."""
import copy
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import app
import planning
from server.model_queue import ModelQueue
from ddl_text.codex_client import ENV_KEYS


class PlanningRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.original_db = app.DB
        app.DB = Path(cls.folder.name) / 'planning.sqlite3'
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
        self.env = patch.dict(planning.os.environ, {**dict.fromkeys(ENV_KEYS, ''),
            'HARNESS_URL': '', 'HARNESS_TOKEN': '', 'DDL_PROVIDER': 'qiniu'})
        self.env.start()
        self.addCleanup(self.env.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = patch.object(planning, 'ROOT', Path(folder.name))
        self.root.start()
        self.addCleanup(self.root.stop)
        day = (app.datetime.now(app.TZ) + app.timedelta(days=1)).date().isoformat()
        self.task = dict(id='synthetic', title='报告', description='合成测试，不是私人作业',
                         status='not_started', plans=[], subtasks=[], due=dict(date=day, precision='date'))
        self.proposal = dict(estimates={'synthetic': 60}, blocks=[dict(taskId='synthetic', title='写报告',
            startAt=day+'T19:00:00+08:00', endAt=day+'T20:00:00+08:00')], warnings=[])
        self.client = self.new_client(self.id())

    def call(self, client, path, data=None, method=None):
        try:
            response = client.open(Request(self.base + path,
                None if data is None else json.dumps(data).encode(),
                {'Content-Type': 'application/json'}, method=method), timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def new_client(self, name):
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        self.assertEqual(self.call(client, '/auth/register', dict(
            name='Synthetic planning', email=name+'@planning.test', password='Synthetic-only-2026!'))[0], 200)
        self.assertEqual(self.call(client, '/data/tasks', dict(value=[self.task], version=0), 'PUT')[0], 200)
        self.assertEqual(self.call(client, '/data/availability', dict(value=dict(
            startTime='19:00', endTime='22:00', weekdays=list(range(7))), version=0), 'PUT')[0], 200)
        return client

    def propose(self, client=None):
        return self.call(client or self.client, '/planning/proposals', {})

    def test_direct_generate_uses_root_env_and_preserves_preview_apply_isolation(self):
        for key in ENV_KEYS:
            planning.os.environ.pop(key, None)
        (planning.ROOT / '.env').write_text('DDL_PROVIDER=qiniu\nQINIU_API_KEY=synthetic-file-key\n'
            'DDL_MODEL=synthetic-model\n', encoding='utf-8')
        other = self.new_client(self.id()+'-other')
        with patch.object(planning.planning_harness.ChatClient, 'complete_json',
                          return_value=json.dumps(self.proposal)) as model, \
                patch.object(planning, 'urlopen', side_effect=AssertionError('HTTP bridge used')):
            status, result = self.propose()
        self.assertEqual(status, 200, result)
        instructions, payload, settings = model.call_args.args
        self.assertEqual(instructions, planning.planning_harness.INSTRUCTIONS)
        self.assertEqual(json.loads(payload)['tasks'], [self.task])
        self.assertGreater(app.datetime.fromisoformat(json.loads(payload)['requestedAt']),app.datetime.now(app.TZ)-app.timedelta(seconds=10))
        self.assertEqual((settings.model, settings.timeout_seconds, settings.max_retries), ('synthetic-model', 20, 0))
        self.assertEqual(self.call(self.client, '/workspace')[1]['tasks']['value'], [self.task])
        self.assertEqual(self.call(other, '/planning/apply', dict(requestId=result['requestId']))[0], 404)
        self.assertEqual(self.call(other, '/workspace')[1]['tasks']['value'], [self.task])
        status, applied = self.call(self.client, '/planning/apply', dict(requestId=result['requestId']))
        self.assertEqual(status, 200)
        self.assertEqual(len(applied['value'][0]['plans']), 1)
        self.assertEqual(applied['value'][0]['due'], self.task['due'])
        self.assertEqual(self.call(self.client, '/planning/apply', dict(requestId=result['requestId']))[0], 409)

    def test_explicit_environment_overrides_file_and_stale_proposal_cannot_apply(self):
        (planning.ROOT / '.env').write_text('DDL_PROVIDER=qiniu\nQINIU_API_KEY=synthetic-file\n', encoding='utf-8')
        with patch.dict(planning.os.environ, {'QINIU_API_KEY': 'synthetic-environment'}), \
                patch.object(planning.planning_harness, 'generate', return_value=self.proposal) as model:
            status, result = self.propose()
        self.assertEqual(status, 200)
        self.assertEqual(model.call_args.args[1]['key'], 'synthetic-environment')
        self.assertEqual(self.call(self.client, '/data/tasks', dict(value=[dict(self.task, title='已修改')], version=1), 'PUT')[0], 200)
        self.assertEqual(self.call(self.client, '/planning/apply', dict(requestId=result['requestId']))[0], 409)

    def test_http_switch_keeps_token_budget_and_does_not_load_local_credentials(self):
        def remote(req, timeout):
            self.assertEqual(req.full_url, 'http://synthetic.invalid/planning')
            self.assertEqual(req.get_header('Authorization'), 'Bearer synthetic-token')
            self.assertEqual(timeout, 25)
            self.assertEqual(json.loads(req.data)['tasks'], [self.task])
            return io.BytesIO(json.dumps(self.proposal).encode())
        with patch.dict(planning.os.environ, {'HARNESS_URL': 'http://synthetic.invalid/planning',
                                              'HARNESS_TOKEN': 'synthetic-token'}), \
                patch.object(planning, 'live_options', side_effect=AssertionError('local credentials read')), \
                patch.object(planning, 'urlopen', side_effect=remote):
            self.assertEqual(self.propose()[0], 200)

    def test_configuration_and_model_failures_do_not_save_and_release_lock(self):
        self.assertEqual(self.propose()[0], 503)
        (planning.ROOT / '.env').write_text('unknown=synthetic-secret', encoding='utf-8')
        status, result = self.propose()
        self.assertEqual(status, 503)
        self.assertNotIn('synthetic-secret', json.dumps(result))
        (planning.ROOT / '.env').write_text('', encoding='utf-8')
        with patch.dict(planning.os.environ, {'QINIU_API_KEY': 'synthetic-key'}):
            for error in [TimeoutError('synthetic-secret'), RuntimeError('synthetic-secret')]:
                with patch.object(planning.planning_harness, 'generate', side_effect=error):
                    status, result = self.propose()
                self.assertEqual(status, 502)
                self.assertNotIn('synthetic-secret', json.dumps(result))
            for invalid in [{}, dict(self.proposal, blocks=[{}]), dict(self.proposal, blocks=[None])]:
                with patch.object(planning.planning_harness, 'generate', return_value=invalid):
                    self.assertEqual(self.propose()[0], 502)
            self.assertEqual(self.call(self.client, '/workspace')[1]['tasks']['value'], [self.task])
            uid = self.call(self.client, '/auth/me')[1]['user']['id']
            with app.connection() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM proposals WHERE user_id=?', (uid,)).fetchone()[0], 0)
            with patch.object(planning.planning_harness, 'generate', return_value=self.proposal):
                self.assertEqual(self.propose()[0], 200)

    def test_http_errors_never_fall_back_to_a_second_paid_call(self):
        with patch.dict(planning.os.environ, {'HARNESS_URL': 'http://synthetic.invalid/planning'}), \
                patch.object(planning.planning_harness, 'generate', side_effect=AssertionError('local fallback')):
            for error, expected in [(HTTPError('http://synthetic.invalid', 503, 'busy', {}, io.BytesIO()), 503),
                                    (TimeoutError('synthetic-secret'), 502)]:
                with patch.object(planning, 'urlopen', side_effect=error):
                    status, result = self.propose()
                self.assertEqual(status, expected)
                self.assertNotIn('synthetic-secret', json.dumps(result))
            for raw in [b'not JSON', b' ' * (planning.MAX_BYTES + 1)]:
                with patch.object(planning, 'urlopen', return_value=io.BytesIO(raw)):
                    self.assertEqual(self.propose()[0], 502)
            with patch.object(planning, 'urlopen', return_value=io.BytesIO(json.dumps(self.proposal).encode())):
                self.assertEqual(self.propose()[0], 200)

    def test_full_queue_is_shared_across_accounts_and_transports(self):
        other = self.new_client(self.id()+'-other')
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test did not release planning')
            return copy.deepcopy(self.proposal)
        with patch.dict(planning.os.environ, {'QINIU_API_KEY': 'synthetic-key'}), \
                patch.object(planning, '_ACTIVE', ModelQueue(limit=1,pending=0)), \
                patch.object(planning.planning_harness, 'generate', side_effect=blocked), \
                patch.object(planning, 'urlopen') as remote, ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.propose)
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.propose(other)[0], 503)
                with patch.dict(planning.os.environ, {'HARNESS_URL': 'http://synthetic.invalid/planning'}):
                    self.assertEqual(self.propose(other)[0], 503)
                remote.assert_not_called()
                self.assertEqual(self.call(other, '/workspace')[0], 200)
                anonymous = build_opener(HTTPCookieProcessor(CookieJar()))
                self.assertEqual(self.propose(anonymous)[0], 401)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)[0], 200)
            self.assertEqual(self.propose(other)[0], 200)


if __name__ == '__main__':
    unittest.main()
