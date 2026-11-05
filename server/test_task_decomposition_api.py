import json
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from unittest.mock import patch
from jsonschema import Draft202012Validator

import app
from auth_test_support import registration_payload

CONTRACTS = Path(__file__).resolve().parent.parent / 'contracts' / 'task-decomposition'
EXAMPLES = json.loads((CONTRACTS / 'examples-v0.1.json').read_text(encoding='utf-8'))
SCHEMA = json.loads((CONTRACTS / 'schema-v0.1.json').read_text(encoding='utf-8'))


def validate_contract(name, value):
    Draft202012Validator({'$schema': SCHEMA['$schema'], '$defs': SCHEMA['$defs'],
                          '$ref': '#/$defs/' + name}).validate(value)

def assessment(complexity):
    return dict(summary='完成剩余项目工作', complexity=complexity, confidence=0.9,
                known_facts=[dict(text='需要完成项目', source='task')],
                assumptions=[], missing_information=[])


def step(identifier, title, duration, dependencies=None):
    return dict(id=identifier, title=title, outcome=title + '可检查', duration_minutes=duration,
                depends_on=dependencies or [], scheduled_start=None, scheduled_end=None,
                source_basis=['task'], assumption=False)


class TaskDecompositionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app.DB = Path(cls.tmp.name) / 'decomposition.sqlite3'
        app.initialize()
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}/api'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def client(self):
        return build_opener(HTTPCookieProcessor(CookieJar()))

    def call(self, client, path, data=None, method='POST'):
        request = Request(self.base + path, None if data is None else json.dumps(data).encode(),
                          {'Content-Type': 'application/json'}, method=method)
        try:
            response = client.open(request)
        except HTTPError as error:
            response = error
        return response.status, json.loads(response.read())

    def register(self, client, email):
        account = registration_payload(dict(name='拆解测试', email=email, password='Test-password-2026'),
                                       lambda body: self.call(client, '/auth/email-code', body))
        status, _ = self.call(client, '/auth/register', account)
        self.assertEqual(status, 200)

    def save_task(self, client, task, version=0):
        return self.call(client, '/data/tasks', dict(value=[task], version=version), 'PUT')

    def test_clarification_then_preview_and_confirmed_apply(self):
        client, other = self.client(), self.client()
        self.register(client, 'decomposition@test.local')
        self.register(other, 'decomposition-other@test.local')
        task = dict(id='project', title='完成课程项目', description='实现任务页并准备展示',
                    status='not_started', plans=[], due=None,
                    subtasks=[dict(id='old', title='旧步骤', status='completed')])
        self.assertEqual(self.save_task(client, task)[0], 200)

        clarification = dict(status='needs_clarification', assessment={
            **assessment('uncertain'), 'missing_information': ['当前进度']},
            questions=[dict(id='progress', prompt='目前完成到哪一步？',
                            why='决定剩余步骤', answer_options=['尚未开始', '已有初稿'])], plan=None)
        with patch.object(app.task_decomposition, 'generate', return_value=clarification) as model:
            status, response = self.call(client, '/decomposition/proposals',
                                         dict(taskId='project', conversation=[]))
        self.assertEqual(status, 200)
        self.assertNotIn('requestId', response)
        self.assertEqual(self.call(client, '/workspace', None, 'GET')[1]['tasks']['value'][0]['subtasks'], task['subtasks'])
        self.assertEqual(model.call_args.args[0]['title'], '完成课程项目')

        ready = dict(status='ready_to_plan', assessment=assessment('multi_step'), questions=[],
                     plan=dict(goal='完成并演示课程项目', strategy='先实现再准备展示', warnings=[],
                               subtasks=[step('implementation', '完成任务页', 80),
                                         step('slides', '制作演示文稿', 40, ['implementation'])]))
        conversation = [dict(role='assistant', content='目前完成到哪一步？'),
                        dict(role='user', content='登录已完成，任务页尚未开始')]
        with patch.object(app.task_decomposition, 'generate', return_value=ready):
            status, response = self.call(client, '/decomposition/proposals',
                                         dict(taskId='project', conversation=conversation))
        self.assertEqual(status, 200)
        self.assertIn('requestId', response)
        self.assertEqual(response['taskId'], 'project')
        self.assertEqual(response['contract_version'], '0.1')
        self.assertEqual(self.call(client, '/decomposition/apply', dict(requestId=response['requestId'], taskId='other'))[0], 409)
        self.assertEqual(self.call(client, '/decomposition/apply', dict(requestId=response['requestId']))[0], 400)
        self.assertEqual(self.call(other, '/decomposition/apply', dict(requestId=response['requestId'], taskId='project'))[0], 404)
        before = self.call(client, '/workspace', None, 'GET')[1]['tasks']['value'][0]
        self.assertEqual(before['subtasks'], task['subtasks'])

        status, applied = self.call(client, '/decomposition/apply', dict(requestId=response['requestId'], taskId='project'))
        self.assertEqual(status, 200)
        updated = applied['value'][0]
        self.assertEqual([item['title'] for item in updated['subtasks']], ['完成任务页', '制作演示文稿'])
        self.assertEqual(updated['estimatedMinutes'], 120)
        self.assertEqual(updated['subtasks'][1]['dependsOn'], [updated['subtasks'][0]['id']])
        self.assertEqual(self.call(client, '/decomposition/apply', dict(requestId=response['requestId'], taskId='project'))[0], 409)

    def test_completed_tasks_bad_conversation_and_stale_preview_are_rejected(self):
        client = self.client()
        self.register(client, 'decomposition-invalid@test.local')
        completed = dict(id='done', title='已完成', status='completed', plans=[], subtasks=[], due=None)
        self.assertEqual(self.save_task(client, completed)[0], 200)
        self.assertEqual(self.call(client, '/decomposition/proposals', dict(taskId='done'))[0], 400)
        self.assertEqual(self.call(client, '/decomposition/proposals',
                                   dict(taskId='done', conversation='bad'))[0], 400)

        active = dict(completed, id='active', title='进行中的项目', status='not_started')
        self.assertEqual(self.save_task(client, active, 1)[0], 200)
        ready = dict(status='ready_to_plan', assessment=assessment('multi_step'), questions=[],
                     plan=dict(goal='完成项目', strategy='分步执行', warnings=[],
                               subtasks=[step('one', '步骤一', 20), step('two', '步骤二', 20)]))
        with patch.object(app.task_decomposition, 'generate', return_value=ready):
            _, response = self.call(client, '/decomposition/proposals', dict(taskId='active'))
        changed = dict(active, description='任务已经修改')
        self.assertEqual(self.save_task(client, changed, 2)[0], 200)
        self.assertEqual(self.call(client, '/decomposition/apply', dict(requestId=response['requestId'], taskId='active'))[0], 409)

    def test_public_contract_examples_round_trip_without_exposing_internal_drafts(self):
        client = self.client()
        self.register(client, 'decomposition-contract@test.local')
        task = dict(id='project', title='完成课程项目', description='', status='not_started',
                    plans=[], subtasks=[], due=None)
        atomic = dict(task, id='atomic', title='提交报告')
        self.assertEqual(self.call(client, '/data/tasks', dict(value=[task, atomic], version=0), 'PUT')[0], 200)
        Draft202012Validator.check_schema(SCHEMA)
        for name in ['clarification', 'no_split', 'ready']:
            example = EXAMPLES[name]
            validate_contract('proposalRequest', example['request'])
            validate_contract('proposalResponse', example['response'])
            draft = {key: example['response'][key] for key in ['status', 'assessment', 'questions', 'plan']}
            app.task_decomposition.validate_model_draft(draft)
            with patch.object(app.task_decomposition, 'generate', return_value={**draft, 'schema_version': '0.1-internal'}):
                status, response = self.call(client, '/decomposition/proposals', example['request'])
            self.assertEqual(status, 200)
            self.assertNotIn('schema_version', response)
            validate_contract('proposalResponse', response)
            if name == 'ready':
                proposal_id = response['requestId']
                response['requestId'] = example['response']['requestId']
            self.assertEqual(response, example['response'])
        status, applied = self.call(client, '/decomposition/apply',
                                    dict(EXAMPLES['apply']['request'], requestId=proposal_id))
        self.assertEqual(status, 200)
        validate_contract('applyRequest', EXAMPLES['apply']['request'])
        validate_contract('applyResponse', EXAMPLES['apply']['response'])
        validate_contract('applyResponse', applied)
        for example in EXAMPLES['errors']:
            validate_contract('errorResponse', example['response'])
        expected = EXAMPLES['apply']['response']['value'][0]
        updated = applied['value'][0]
        for key in ['estimatedMinutes', 'title']:
            self.assertEqual(updated[key], expected[key])
        for actual, expected_step in zip(updated['subtasks'], expected['subtasks']):
            for key in ['title', 'status', 'outcome', 'estimatedMinutes', 'sourceBasis', 'assumption']:
                self.assertEqual(actual[key], expected_step[key])
        self.assertEqual(updated['subtasks'][1]['dependsOn'], [updated['subtasks'][0]['id']])
        self.assertEqual(updated['plans'], [])
        self.assertEqual(applied['value'][1], atomic)


if __name__ == '__main__':
    unittest.main()
