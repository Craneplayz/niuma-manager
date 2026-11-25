"""Real HTTP/SQLite checks for replay, CAS, account boundaries and atomic receipts."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
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

    def quick_operation(self, protected=(), **overrides):
        options = dict(text='下周交试验', timezone='Asia/Shanghai', referenceDay='2026-11-19',
                       protectedFields=list(protected))
        options.update(overrides)
        return dict(self.operation, intent=dict(kind='save', expected=None, draft=dict(id=self.task['id']),
                    createdAt='2026-11-19T08:00:00+08:00', quickRecognition=options))

    def quick_snapshot(self, done=False):
        deadline = time.monotonic() + 3
        while True:
            code, snapshot = self.call('/tasks/quick-recognition')
            self.assertEqual(code, 200)
            if not done or all(t.get('quickRecognition', {}).get('status') != 'pending' for t in snapshot['value']):
                return snapshot
            if time.monotonic() >= deadline:
                self.fail('quick task worker did not finish')
            time.sleep(.015)

    def test_bulk_date_and_undo_keep_quick_recognition_field_protected(self):
        import task_bulk
        import quick_task_jobs
        with patch('quick_task_jobs.wake'):
            self.assertEqual(self.write(operation=self.quick_operation())[0], 200)
            current = self.quick_snapshot()
            intent = dict(kind='bulk', action='deadline', workspaceGeneration=0,
                          tasks=[dict(id='a', expected=task_bulk.snapshot(current['value'][0]))],
                          candidates=[], date='2026-12-01')
            operation = dict(id='bulk-quick-date-0001', owner='alice', intent=intent)
            status, changed = self.call('/tasks/bulk', dict(version=current['version'], operation=operation))
            self.assertEqual(status, 200, changed)
            operation = dict(id='bulk-quick-undo-0001', owner='alice', intent=changed['undo'])
            self.assertEqual(self.call('/tasks/bulk', dict(version=changed['version'], operation=operation))[0], 200)
            with app.connection() as db:
                job = db.execute('SELECT * FROM quick_task_jobs').fetchone()
                self.assertIn('due', json.loads(job['request'])['protectedFields'])
                quick_task_jobs.settle(db, job, dict(title='合成整理', due=dict(date='2026-12-02', precision='date')))
            saved, = self.quick_snapshot()['value']
            self.assertIsNone(saved['due'])
            self.assertEqual(saved['title'], '合成整理')

    def test_local_summary_saves_original_without_scheduling_recognition(self):
        source = '明早10点到报告厅参加说明会'
        operation = self.quick_operation(text=source, localOnly=True)
        task = dict(self.task, title='参加说明会', due=None, description='地点：报告厅',
                    eventAt=dict(date='2026-11-20', precision='datetime', time='10:00'))
        with patch('quick_task_jobs.wake') as wake:
            for _ in range(2):
                code, saved = self.write(operation=operation, tasks=[task])
                self.assertEqual(code, 200)
                self.assertEqual(saved['value'], [dict(task, quickRecognition=dict(text=source, status='done'))])
            wake.assert_not_called()
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM quick_task_jobs').fetchone()[0], 0)

    def test_quick_create_receipt_worker_and_ai_only_conflict(self):
        operation = self.quick_operation()
        release, started = threading.Event(), threading.Event()
        def recognize(source, baseline):
            self.assertEqual(source['timezone'], 'Asia/Shanghai')
            self.assertEqual(source['referenceDay'], '2026-11-19')
            self.assertEqual(baseline['title'], self.task['title'])
            started.set()
            self.assertTrue(release.wait(3))
            return dict(title='交实验报告', course='实验课')
        with patch('quick_task_recognition.recognize_quick_task', side_effect=recognize) as model:
            try:
                status, written = self.write(operation=operation)
                self.assertEqual(status, 200)
                self.assertEqual(written['value'][0]['quickRecognition']['status'], 'pending')
                self.assertTrue(started.wait(2))
                self.assertEqual(self.write(operation=operation)[1]['replayed'], True)
                self.assertEqual(self.call('/tasks/quick-recognition', user='bob')[1]['value'], [])
            finally:
                release.set()
            snapshot = self.quick_snapshot(done=True)
            self.assertEqual(model.call_count, 1)
        self.assertEqual((snapshot['version'],snapshot['owner'],snapshot['generation']), (2,'alice',0))
        task, = snapshot['value']
        self.assertEqual((task['id'],task['title'],task['course']), ('a','交实验报告','实验课'))
        self.assertEqual(task['quickRecognition'], dict(status='done',text='下周交试验',
                         applied=dict(title='交实验报告',course='实验课'),
                         appliedFrom=dict(title=self.task['title'],course=None)))
        edit = dict(self.operation, id='edit-0000000000001')
        stale, error = self.write(1, edit)
        self.assertEqual((stale,error['code'],error['quickVersion']), (409,'quick_recognition_changed',2))
        updated = dict(task, title='我写的')
        self.assertEqual(self.write(2, edit, [updated])[1]['value'][0]['quickRecognition']['applied'], dict(course='实验课'))
        stale, error = self.write(1, dict(edit, id='edit-0000000000002'))
        self.assertEqual(stale, 409)
        self.assertNotIn('code', error)
        latest = self.write(operation=operation)[1]
        self.assertTrue(latest['replayed'])
        self.assertEqual(latest['value'][0]['title'], '我写的')

    def test_quick_job_protects_preview_date_and_changed_then_restored_course(self):
        operation = self.quick_operation(protected=['due'])
        release, started = threading.Event(), threading.Event()
        def recognize(*_):
            started.set()
            self.assertTrue(release.wait(3))
            return dict(title='模型整理标题', due=dict(date='2026-11-24',precision='date'), course='课程A')
        with patch('quick_task_recognition.recognize_quick_task', side_effect=recognize):
            try:
                self.assertEqual(self.write(operation=operation)[0], 200)
                self.assertTrue(started.wait(2))
                task, = self.quick_snapshot()['value']
                # Legacy/full-list clients may omit all new metadata on edits.
                task.pop('quickRecognition')
                for version, course in ((1, '手动课程'), (2, None)):
                    task['course'] = course
                    status, _ = self.call('/data/tasks', dict(value=[task],version=version),method='PUT')
                    self.assertEqual(status, 200)
            finally:
                release.set()
            task, = self.quick_snapshot(done=True)['value']
        self.assertEqual((task['title'],task['course'],task['due']), ('模型整理标题',None,None))
        self.assertEqual(task['quickRecognition']['applied'], dict(title='模型整理标题'))

    def test_quick_model_failure_and_invalid_creation(self):
        malformed = self.quick_operation(timezone='Invalid/Zone')
        self.assertEqual(self.write(operation=malformed)[0], 400)
        self.assertEqual(self.quick_snapshot()['value'], [])
        operation = self.quick_operation()
        with patch('quick_task_recognition.recognize_quick_task', side_effect=ValueError('provider failure')):
            self.assertEqual(self.write(operation=operation)[0], 200)
            task, = self.quick_snapshot(done=True)['value']
        self.assertEqual(task['quickRecognition'], dict(text='下周交试验',status='failed'))
        self.assertEqual(task['title'], self.task['title'])

    def test_deleted_quick_job_cannot_overwrite_recreated_task(self):
        release, started = threading.Event(), threading.Event()
        def recognize(*_):
            started.set()
            self.assertTrue(release.wait(3))
            return dict(title='已删除任务的结果')
        with patch('quick_task_recognition.recognize_quick_task', side_effect=recognize):
            try:
                self.assertEqual(self.write(operation=self.quick_operation())[0], 200)
                self.assertTrue(started.wait(2))
                self.assertEqual(self.call('/data/tasks', dict(ids=['a'],version=1), method='DELETE')[0], 200)
                self.assertEqual(self.write(2, dict(self.operation,id='replacement-000001'))[0], 200)
            finally:
                release.set()
            from quick_task_jobs import _lock, _workers
            with _lock:
                workers = list(_workers.get(app.DB, ()))
            for thread in workers:
                thread.join(2)
        self.assertEqual(self.quick_snapshot()['value'], [self.task])

    def test_restart_handles_queued_and_interrupted_jobs(self):
        with patch('quick_task_jobs.wake'):
            self.write(operation=self.quick_operation())
        with patch.dict('os.environ', NIUMA_BACKGROUND_RECOVERY='1'), patch(
                'quick_task_recognition.recognize_quick_task', return_value={}) as recognize:
            app.initialize()
            self.assertEqual(self.quick_snapshot(done=True)['value'][0]['quickRecognition']['status'], 'done')
            self.assertEqual(recognize.call_count, 1)
        self.assertEqual(self.call('/data/tasks', dict(ids=['a'],version=2), method='DELETE')[0], 200)
        operation = dict(self.quick_operation(), id='interrupted-000001')
        with patch('quick_task_jobs.wake'):
            self.assertEqual(self.write(3, operation)[0], 200)
        with app.connection() as db:
            db.execute("UPDATE quick_task_jobs SET state='running'")
        with patch('quick_task_recognition.recognize_quick_task') as recognize:
            app.initialize()
            self.assertEqual(self.quick_snapshot(done=True)['value'][0]['quickRecognition']['status'], 'failed')
            recognize.assert_not_called()

    def test_background_recovery_disabled_leaves_task_available_without_model_call(self):
        with patch('quick_task_jobs.wake'):
            self.assertEqual(self.write(operation=self.quick_operation())[0], 200)
        with patch.dict('os.environ', NIUMA_BACKGROUND_RECOVERY='0'), patch(
                'quick_task_recognition.recognize_quick_task') as recognize:
            app.initialize()
            stored, = self.quick_snapshot(done=True)['value']
            self.assertEqual(stored['quickRecognition'], dict(status='failed',text='下周交试验'))
            self.assertEqual(stored['title'], self.task['title'])
            recognize.assert_not_called()

    def test_worker_thread_cannot_start_keeps_local_task(self):
        with patch('quick_task_jobs.wake'):
            self.assertEqual(self.write(operation=self.quick_operation())[0], 200)
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('synthetic')):
            app.quick_task_jobs.wake(app.DB)
        self.assertEqual(self.quick_snapshot(done=True)['value'][0]['quickRecognition']['status'], 'failed')

    def test_shared_contract_example_matches_server_validation(self):
        from jsonschema import Draft202012Validator
        contracts = Path(__file__).resolve().parents[1] / 'contracts'
        schema = json.loads((contracts/'schemas/task-writes-v0.1.schema.json').read_text())
        examples = json.loads((contracts/'examples/task-writes-v01.json').read_text())
        for name in ('operation','quickTaskOperation','reconcile'): Draft202012Validator(schema).validate(examples[name])
        operation = examples['operation']
        self.assertEqual(task_operations.identity(operation, operation['owner'], app.ApiError)[0], operation['id'])
        for bad in (dict(operation, id='short'), dict(operation, intent=dict(kind='other')),
                    dict(operation, intent=dict(kind='status', workspaceGeneration=True))):
            self.assertFalse(Draft202012Validator(schema).is_valid(bad))
            with self.assertRaises(app.ApiError): task_operations.identity(bad, operation['owner'], app.ApiError)


if __name__ == '__main__': unittest.main()
