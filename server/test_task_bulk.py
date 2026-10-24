"""Real HTTP/SQLite regressions for atomic mixed task/candidate bulk writes."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener

import app
import task_bulk
import task_operations
from contracts import task_bulk as contract


class TaskBulkTests(unittest.TestCase):
    def setUp(self):
        self.old_db = app.DB
        self.tmp = tempfile.TemporaryDirectory()
        app.DB = Path(self.tmp.name) / 'bulk.sqlite3'
        self.env = patch.dict(os.environ, NIUMA_ENVIRONMENT='test', NIUMA_COOKIE_SECURE='0')
        self.env.start()
        app.initialize(recover=False)
        self.serial = 0
        self.task = dict(id='ordinary', title='合成普通任务', description='保留已有说明', course='数据结构',
                         content_type='task', status='in_progress', due=dict(date='2026-10-25', precision='datetime', time='18:30'),
                         plans=[], subtasks=[], metadata=dict(untouched=True))
        fixture = json.loads((Path(__file__).parents[1] / 'contracts/text-to-app/text-contract-v0.2.json').read_text())
        self.request = fixture['request']
        self.candidate = deepcopy(fixture['expected']['tasks'][0])
        self.candidate.update(title='合成待核对任务', description='候选原文摘要', notice_type='create', content_type='task',
                              needs_review=True, review_reasons=['需要确认'],
                              deadline=dict(kind='deadline', status='explicit', precision='date', value='2026-10-26'))
        self.result = fixture['expected']
        self.result['tasks'] = [self.candidate]
        with app.connection() as db:
            for uid in ('alice', 'bob'):
                db.execute('INSERT INTO users VALUES(?,?,?,?,?)', (uid, uid + '@example.test', uid, '00' * 16, 'unused'))
                db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(uid.encode()).hexdigest(), uid, time.time() + 3600))
            db.execute('INSERT INTO workspace VALUES(?,?,?,?)', ('alice', 'tasks', json.dumps([self.task]), 1))
            db.execute('INSERT INTO recognition_batches(user_id,request_id,fingerprint,request,result,demo) VALUES(?,?,?,?,?,0)',
                       ('alice', self.request['request_id'], 'synthetic', json.dumps(self.request), json.dumps(self.result)))
            db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',
                       ('pending', 'alice', self.request['request_id'], json.dumps(self.candidate)))
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        app.DB = self.old_db
        self.env.stop()
        self.tmp.cleanup()

    def call(self, path, body=None, uid='alice', method='POST'):
        request = Request(f'http://127.0.0.1:{self.server.server_port}/api{path}',
                          None if body is None else json.dumps(body).encode(),
                          {'Content-Type': 'application/json', 'Cookie': app.runtime_environment.cookie_name() + '=' + uid},
                          method='GET' if body is None else method)
        try:
            response = build_opener().open(request)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def state(self):
        with app.connection() as db:
            return task_operations.workspace(db, 'alice'), [dict(row) for row in db.execute(
                'SELECT id,state,version FROM recognition_candidates WHERE user_id="alice" ORDER BY id')]

    def body(self, action, *, tasks=True, candidates=True, intent=None, **values):
        self.serial += 1
        workspace, _ = self.state()
        if intent is None:
            intent = dict(kind='bulk', action=action, workspaceGeneration=0,
                          tasks=[dict(id=task['id'], expected=task_bulk.snapshot(task)) for task in workspace['value']] if tasks else [],
                          candidates=[dict(id='pending', version=1, taskId='newtask0000000001', expected=task_bulk.candidate_snapshot(self.candidate))] if candidates else [])
            intent.update(dict(complete=dict(completedAt='2026-10-24'), deadline=dict(date='2026-10-30'),
                               trash=dict(deletedAt='2026-10-24T01:00:00Z')).get(action, {}))
            intent.update(values)
        return dict(version=workspace['version'], operation=dict(id=f'bulk-operation-{self.serial:04d}', owner='alice', intent=intent))

    def send(self, body):
        status, result = self.call('/tasks/bulk', body)
        self.assertEqual(status, 200, result)
        contract.check('response', result)
        return result

    def test_mixed_completion_is_one_version_and_undo_keeps_candidate_as_an_ordinary_task(self):
        result = self.send(self.body('complete'))
        self.assertEqual(result['version'], 2)
        self.assertEqual(len(result['value']), 2)
        for task in result['value']:
            self.assertEqual((task['status'], task['completedAt']), ('completed', '2026-10-24'))
        created = result['value'][1]
        self.assertEqual(created['id'], 'newtask0000000001')
        self.assertEqual(created['recognitionHistory'][0]['storageId'], 'pending')
        self.assertTrue(created['source'])
        self.assertEqual(result['value'][0]['metadata'], dict(untouched=True))
        self.assertEqual(result['candidates'], [dict(id='pending', state='applied', version=2, taskId=created['id'])])
        restored = self.send(self.body('undo', intent=result['undo']))
        self.assertEqual(restored['version'], 3)
        self.assertEqual([task['status'] for task in restored['value']], ['in_progress', 'not_started'])
        self.assertEqual(restored['batches'][0]['items'][0]['state'], 'applied')
        self.assertIsNone(restored['undo'])

    def test_mixed_deadline_preserves_each_clock_and_undo_restores_previous_dates(self):
        self.candidate['deadline'] = dict(kind='deadline', status='explicit', precision='datetime', value='2026-10-26T20:15:00+08:00')
        with app.connection() as db:
            db.execute('UPDATE recognition_candidates SET candidate=? WHERE id="pending"', (json.dumps(self.candidate),))
        result = self.send(self.body('deadline'))
        self.assertEqual([task['due'] for task in result['value']], [dict(date='2026-10-30', precision='datetime', time='18:30'),
                                                                 dict(date='2026-10-30', precision='datetime', time='20:15')])
        restored = self.send(self.body('undo', intent=result['undo']))
        self.assertEqual([task['due']['date'] for task in restored['value']], ['2026-10-25', '2026-10-26'])
        self.assertEqual(self.state()[1][0]['state'], 'applied')

    def test_inferred_candidate_date_remains_unconfirmed_and_clear_date_removes_time(self):
        self.candidate['deadline'].update(status='inferred')
        with app.connection() as db:
            db.execute('UPDATE recognition_candidates SET candidate=? WHERE id="pending"', (json.dumps(self.candidate),))
        self.assertEqual(task_bulk.candidate_snapshot(self.candidate)['due'], '')
        result = self.send(self.body('deadline', date=''))
        self.assertTrue(all(task['due'] is None for task in result['value']))
        restored = self.send(self.body('undo', intent=result['undo']))
        self.assertIsNone(restored['value'][1]['due'])

    def test_mixed_trash_and_undo_restore_candidate_pending_with_strict_versions(self):
        result = self.send(self.body('trash'))
        self.assertEqual(result['version'], 2)
        self.assertEqual(result['value'][0]['recycleBin']['status'], 'in_progress')
        self.assertEqual(self.state()[1], [dict(id='pending', state='ignored', version=2)])
        restored = self.send(self.body('undo', intent=result['undo']))
        self.assertEqual(restored['value'], [self.task])
        self.assertEqual(self.state()[1], [dict(id='pending', state='pending', version=3)])
        self.assertEqual(restored['candidates'], [dict(id='pending', state='pending', version=3)])

    def test_restore_and_reopen_keep_ordinary_metadata(self):
        self.send(self.body('trash', candidates=False))
        restored = self.send(self.body('restore', candidates=False))
        self.assertEqual(restored['value'], [self.task])
        self.send(self.body('complete', candidates=False))
        reopened = self.send(self.body('reopen', candidates=False))
        self.assertEqual(reopened['value'][0]['status'], 'not_started')
        self.assertEqual(reopened['value'][0]['completedAt'], '')

    def test_target_status_skips_preserve_original_completion_day_and_in_progress_state(self):
        self.send(self.body('complete', candidates=False))
        completed = self.body('complete', candidates=False, completedAt='2026-10-25')
        skipped = self.send(completed)
        self.assertEqual(skipped['value'][0]['completedAt'], '2026-10-24')
        self.assertIsNone(skipped['undo'])
        self.send(self.body('reopen', candidates=False))
        with app.connection() as db:
            workspace = task_operations.workspace(db, 'alice')
            workspace['value'][0]['status'] = 'in_progress'
            task_bulk.write(db, 'alice', workspace['value'], workspace['version'] + 1)
        self.assertEqual(self.send(self.body('reopen', candidates=False))['value'][0]['status'], 'in_progress')

    def test_two_candidates_and_an_ordinary_task_commit_with_one_final_version(self):
        with app.connection() as db:
            db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',
                       ('pending-second', 'alice', self.request['request_id'], json.dumps(self.candidate)))
        body = self.body('complete')
        other = deepcopy(body['operation']['intent']['candidates'][0])
        other.update(id='pending-second', taskId='newtask0000000002')
        body['operation']['intent']['candidates'].append(other)
        result = self.send(body)
        self.assertEqual(result['version'], 2)
        self.assertEqual(len(result['value']), 3)
        self.assertEqual({task['id'] for task in result['value']}, {'ordinary', 'newtask0000000001', 'newtask0000000002'})
        self.assertTrue(all(task['completedAt'] == '2026-10-24' for task in result['value']))

    def test_lost_response_replay_returns_current_tasks_stable_undo_and_never_duplicates(self):
        body = self.body('complete')
        result = self.send(body)
        later = self.send(self.body('deadline', candidates=False))
        replay = self.send(body)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['value'], later['value'])
        self.assertEqual(replay['version'], later['version'])
        self.assertEqual(replay['undo'], result['undo'])
        self.assertEqual(len(replay['value']), 2)
        status, reconciled = self.call('/tasks/reconcile', dict(owner='alice', operations=[body['operation']]))
        self.assertEqual(status, 200)
        self.assertEqual(reconciled['applied'], [body['operation']['id']])
        self.assertEqual(reconciled['candidates'][0]['state'], 'applied')
        self.assertEqual(reconciled['bulkResults'], [dict(operationId=body['operation']['id'], undo=result['undo'])])
        changed = deepcopy(body)
        changed['operation']['intent']['completedAt'] = '2026-10-25'
        self.assertEqual(self.call('/tasks/bulk', changed)[0], 409)

    def test_candidate_conflict_and_late_failure_roll_back_entire_batch_and_receipt(self):
        before = self.state()
        stale = self.body('complete')
        stale['operation']['intent']['candidates'][0]['version'] = 99
        self.assertEqual(self.call('/tasks/bulk', stale)[0], 409)
        self.assertEqual(self.state(), before)
        body = self.body('complete')
        with patch('submission_runtime.candidate_applied', side_effect=RuntimeError('synthetic rollback')):
            self.assertEqual(self.call('/tasks/bulk', body)[0], 502)
        self.assertEqual(self.state(), before)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_write_receipts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_bulk_undo').fetchone()[0], 0)
        self.assertEqual(self.send(body)['version'], 2)

    def test_receipt_failure_rolls_back_candidates_tasks_and_inverse_then_retries(self):
        before = self.state()
        body = self.body('trash')
        with app.connection() as db:
            db.execute("CREATE TRIGGER reject_bulk_receipt BEFORE INSERT ON task_write_receipts BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.assertEqual(self.call('/tasks/bulk', body)[0], 502)
        self.assertEqual(self.state(), before)
        with app.connection() as db:
            db.execute('DROP TRIGGER reject_bulk_receipt')
        self.assertEqual(self.send(body)['version'], 2)

    def test_undo_conflict_never_overwrites_later_date_or_reopens_reignored_candidate(self):
        result = self.send(self.body('deadline'))
        self.send(self.body('deadline', candidates=False, date='2026-11-01'))
        before = self.state()
        self.assertEqual(self.call('/tasks/bulk', self.body('undo', intent=result['undo']))[0], 409)
        self.assertEqual(self.state(), before)

    def test_undo_trash_rejects_reignored_candidate_without_restoring_ordinary_task(self):
        result = self.send(self.body('trash'))
        self.assertEqual(self.call('/recognition/candidates/pending/resolve', dict(action='reopen', version=2))[0], 200)
        self.assertEqual(self.call('/recognition/candidates/pending/resolve', dict(action='ignore', version=3))[0], 200)
        before = self.state()
        self.assertEqual(self.call('/tasks/bulk', self.body('undo', intent=result['undo']))[0], 409)
        self.assertEqual(self.state(), before)

    def test_undo_completion_preserves_unrelated_edit_but_rejects_new_bin_state(self):
        result = self.send(self.body('complete', candidates=False))
        with app.connection() as db:
            workspace = task_operations.workspace(db, 'alice')
            workspace['value'][0]['title'] = '稍后修改的标题'
            task_bulk.write(db, 'alice', workspace['value'], workspace['version'] + 1)
        restored = self.send(self.body('undo', intent=result['undo']))
        self.assertEqual(restored['value'][0]['title'], '稍后修改的标题')
        self.assertEqual(restored['value'][0]['status'], 'in_progress')
        next_result = self.send(self.body('deadline', candidates=False))
        self.send(self.body('trash', candidates=False))
        before = self.state()
        self.assertEqual(self.call('/tasks/bulk', self.body('undo', intent=next_result['undo']))[0], 409)
        self.assertEqual(self.state(), before)

    def test_wrong_account_generation_and_stale_snapshot_are_rejected(self):
        body = self.body('complete')
        self.assertEqual(self.call('/tasks/bulk', body, uid='bob')[0], 409)
        other = deepcopy(body)
        other['operation']['owner'] = 'bob'
        self.assertEqual(self.call('/tasks/bulk', other, uid='bob')[0], 409)
        stale = deepcopy(body)
        stale['operation']['intent']['tasks'][0]['expected']['notes'] = 'old text'
        self.assertEqual(self.call('/tasks/bulk', stale)[0], 409)
        result = self.send(body)
        with app.connection() as db:
            task_operations.invalidate_pending(db, 'alice')
            db.execute('DELETE FROM task_bulk_undo WHERE user_id="alice"')
            task_bulk.write(db, 'alice', [], 3)
        self.assertIsNone(self.send(body)['undo'])
        recovery = self.call('/tasks/reconcile', dict(owner='alice', operations=[body['operation']]))[1]
        self.assertEqual(recovery['bulkResults'], [dict(operationId=body['operation']['id'], undo=None)])
        self.assertEqual(self.call('/tasks/bulk', self.body('undo', intent=result['undo']))[0], 409)

    def test_reconcile_returns_only_applied_bulk_results_and_no_redo_for_undo(self):
        original = self.body('trash')
        result = self.send(original)
        inverse_body = self.body('undo', intent=result['undo'])
        self.send(inverse_body)
        unknown = self.body('deadline')
        recovery = self.call('/tasks/reconcile', dict(owner='alice', operations=[original['operation'], inverse_body['operation'], unknown['operation']]))[1]
        self.assertEqual({entry['operationId'] for entry in recovery['bulkResults']},
                         {original['operation']['id'], inverse_body['operation']['id']})
        self.assertIsNone(next(entry for entry in recovery['bulkResults'] if entry['operationId'] == inverse_body['operation']['id'])['undo'])
        schema = json.loads((Path(__file__).parents[1] / 'contracts/schemas/task-writes-v0.1.schema.json').read_text())
        from jsonschema import Draft202012Validator
        Draft202012Validator(schema).validate(recovery)

    def test_new_candidate_only_boundary_and_bulk_cannot_use_ordinary_put(self):
        for change in ('update', 'postpone', 'cancel'):
            candidate = dict(self.candidate, notice_type=change)
            with app.connection() as db:
                db.execute('UPDATE recognition_candidates SET candidate=? WHERE id="pending"', (json.dumps(candidate),))
            self.assertEqual(self.call('/tasks/bulk', self.body('complete'))[0], 400)
        body = self.body('trash', candidates=False)
        self.assertEqual(self.call('/data/tasks', dict(value=[self.task], version=1, operation=body['operation']), method='PUT')[0], 400)

    def test_invalid_duplicate_oversized_or_empty_batch_never_changes_data(self):
        before = self.state()
        for kind in ('empty', 'duplicate', 'date', 'oversized', 'candidate_restore'):
            body = self.body('deadline')
            intent = body['operation']['intent']
            if kind == 'empty': intent.update(tasks=[], candidates=[])
            elif kind == 'duplicate': intent['tasks'] *= 2
            elif kind == 'date': intent['date'] = '2026-02-30'
            elif kind == 'oversized': intent['tasks'][0]['expected']['notes'] = '长' * 40000
            elif kind == 'candidate_restore': intent['action'] = 'restore'
            self.assertEqual(self.call('/tasks/bulk', body)[0], 400, kind)
            self.assertEqual(self.state(), before)

    def test_inverse_storage_contains_no_task_titles_or_descriptions(self):
        self.send(self.body('deadline'))
        with app.connection() as db:
            inverse = db.execute('SELECT inverse FROM task_bulk_undo').fetchone()[0]
            self.assertNotIn(self.task['title'], inverse)
            self.assertNotIn(self.task['description'], inverse)
            self.assertNotIn(self.candidate['description'], inverse)


if __name__ == '__main__':
    unittest.main()
