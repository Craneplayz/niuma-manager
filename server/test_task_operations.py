"""Real HTTP/SQLite checks for replay, CAS, account boundaries and atomic receipts."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor, Request
from urllib.error import HTTPError

import app
import task_operations


class TaskOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app.DB = Path(self.tmp.name) / 'tasks.sqlite3'
        app.initialize()
        with app.connection() as db:
            for uid in ('alice', 'bob'):
                db.execute('INSERT INTO users VALUES(?,?,?,?,?)', (uid, uid+'@test.local', uid, '00'*16, 'unused'))
                db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(uid.encode()).hexdigest(), uid, time.time()+3600))
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = 'http://127.0.0.1:'+str(self.server.server_port)+'/api'
        self.task = dict(id='a', title='合成任务', status='not_started', plans=[], subtasks=[], due=None)
        self.operation = dict(id='0123456789abcdef', owner='alice', intent=dict(kind='save', draft=dict(title='合成任务')))

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join(); self.tmp.cleanup()

    def call(self, path, body=None, user='alice', method='POST'):
        request = Request(self.url+path, None if body is None else json.dumps(body).encode(),
                          {'Cookie': app.runtime_environment.cookie_name()+'='+user, 'Content-Type': 'application/json'},
                          method='GET' if body is None else method)
        try: response = build_opener().open(request)
        except HTTPError as error: response = error
        with response: return response.status, json.loads(response.read())

    def write(self, version=0, operation=None, tasks=None, user='alice'):
        return self.call('/data/tasks', dict(value=tasks if tasks is not None else [self.task], version=version,
                        operation=operation or self.operation), user, 'PUT')

    def test_lost_ack_replays_once_even_after_other_writes(self):
        self.assertEqual(self.write()[1], dict(version=1, operationId=self.operation['id']))
        other = dict(self.operation, id='1111111111111111', intent=dict(kind='status', next='completed'))
        updated = dict(self.task, status='completed', serverMetadata='retained')
        self.assertEqual(self.write(1, other, [updated])[0], 200)
        status, replay = self.write()  # Old full payload/version must never overwrite the second write.
        self.assertEqual(status, 200); self.assertEqual(replay['version'], 2)
        self.assertTrue(replay['replayed']); self.assertEqual(replay['value'], [updated])
        status, recovery = self.call('/tasks/reconcile', dict(owner='alice', operations=[self.operation, other]))
        self.assertEqual(status, 200); self.assertEqual(len(recovery['applied']), 2)
        self.assertEqual(recovery['tasks'], dict(value=[updated], version=2))

    def test_changed_intent_or_wrong_account_is_rejected(self):
        self.assertEqual(self.call('/workspace')[1]['taskWriteOwner'], 'alice')
        self.assertEqual(self.call('/workspace', user='bob')[1]['taskWriteOwner'], 'bob')
        self.assertEqual(self.write()[0], 200)
        changed = dict(self.operation, intent=dict(kind='trash', ids=['a']))
        self.assertEqual(self.write(operation=changed)[0], 409)
        self.assertEqual(self.write(user='bob')[0], 409)
        self.assertEqual(self.call('/tasks/reconcile', dict(owner='alice', operations=[self.operation]), 'bob')[0], 409)
        self.assertNotIn('tasks', self.call('/workspace', user='bob')[1])

    def test_conflict_and_transaction_failure_do_not_create_receipts(self):
        self.assertEqual(self.write(99)[0], 409)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_write_receipts').fetchone()[0], 0)
            db.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON task_write_receipts BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.assertGreaterEqual(self.write()[0], 500)
        self.assertNotIn('tasks', self.call('/workspace')[1])
        with app.connection() as db:
            db.execute('DROP TRIGGER reject_receipt')
        self.assertEqual(self.write()[0], 200)

    def test_same_operation_id_is_independent_for_another_user(self):
        self.assertEqual(self.write()[0], 200)
        self.assertEqual(self.write(operation=dict(self.operation, owner='bob'), user='bob')[0], 200)
        self.assertEqual(self.call('/tasks/reconcile', dict(owner='bob', operations=[dict(self.operation, owner='bob')]), 'bob')[1]['applied'], [self.operation['id']])

    def test_reset_rejects_unsent_old_operations_but_can_acknowledge_already_committed_ones(self):
        self.assertEqual(self.write()[0], 200)
        with app.connection() as db:
            task_operations.invalidate_pending(db, 'alice')
            db.execute('UPDATE workspace SET value="[]",version=2 WHERE user_id="alice" AND name="tasks"')
        unknown = dict(self.operation, id='2222222222222222')
        self.assertEqual(self.write(2, unknown)[0], 409)
        self.assertEqual(self.write()[1]['value'], [])
        fresh = dict(unknown, intent=dict(unknown['intent'], workspaceGeneration=1))
        self.assertEqual(self.write(2, fresh)[0], 200)

    def test_shared_contract_example_matches_server_validation(self):
        from jsonschema import Draft202012Validator
        contracts = Path(__file__).resolve().parents[1] / 'contracts'
        schema = json.loads((contracts/'schemas/task-writes-v0.1.schema.json').read_text())
        examples = json.loads((contracts/'examples/task-writes-v01.json').read_text())
        for name in ('operation','reconcile'): Draft202012Validator(schema).validate(examples[name])
        operation = examples['operation']
        self.assertEqual(task_operations.identity(operation, operation['owner'], app.ApiError)[0], operation['id'])
        for bad in (dict(operation, id='short'), dict(operation, intent=dict(kind='other')),
                    dict(operation, intent=dict(kind='status', workspaceGeneration=True))):
            self.assertFalse(Draft202012Validator(schema).is_valid(bad))
            with self.assertRaises(app.ApiError): task_operations.identity(bad, operation['owner'], app.ApiError)


if __name__ == '__main__': unittest.main()
