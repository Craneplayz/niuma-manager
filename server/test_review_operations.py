"""Real HTTP/SQLite candidate queue receipts, replay and target guards."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

import app
import task_operations
import test_task_operations as task_tests


class ReviewOperationTests(unittest.TestCase):
    call = task_tests.TaskOperationTests.call
    write = task_tests.TaskOperationTests.write
    tearDown = task_tests.TaskOperationTests.tearDown

    def setUp(self):
        task_tests.TaskOperationTests.setUp(self)
        fixture = json.loads((Path(__file__).resolve().parents[1] / 'contracts/text-to-app/text-contract-v0.2.json').read_text())
        self.candidate = deepcopy(fixture['expected']['tasks'][0])
        self.candidate.update(needs_review=True, review_reasons=['合成核对'])
        self.candidate['notice_type'] = 'create'
        with app.connection() as db:
            db.execute('INSERT INTO recognition_batches(user_id,request_id,fingerprint,request,result,demo) VALUES(?,?,?,?,?,0)',
                       ('alice', 'review-fixture', 'synthetic', json.dumps(fixture['request']), json.dumps(fixture['expected'])))
            self.add_candidate(db, 'candidate-a')

    def add_candidate(self, db, cid, kind='create', user='alice'):
        candidate = dict(self.candidate, notice_type=kind)
        db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',
                   (cid, user, 'review-fixture', json.dumps(candidate)))

    def review_operation(self, *, oid='review-operation-0001', cid='candidate-a', action='create', version=1, **fields):
        intent = dict(kind='review', candidateId=cid, action=action, expectedCandidateVersion=version, workspaceGeneration=0)
        if action in ('create', 'update', 'postpone'):
            intent['draft'] = dict(title='合成核对任务', description='合成说明', course=None, content_type='task', due=None)
        if action == 'create':
            intent.update(newTaskId='stable-review-task-0001', createdAt='2026-10-23T01:00:00+00:00')
        intent.update(fields)
        return dict(id=oid, owner='alice', intent=intent)

    def resolve(self, operation, tasks_version=0, user='alice', **changes):
        intent = operation['intent']
        body = dict(action=intent['action'], version=intent['expectedCandidateVersion'], operation=operation)
        if intent['action'] not in ('ignore', 'reopen'):
            body.update(tasks_version=tasks_version, reviewed=True, target_id=intent.get('targetId'))
        for name in ('draft', 'complete'):
            if name in intent:
                body[name] = deepcopy(intent[name])
        body.update(changes)
        return self.call('/recognition/candidates/' + intent['candidateId'] + '/resolve', body, user)

    def candidate_state(self):
        with app.connection() as db:
            return dict(db.execute('SELECT state,version FROM recognition_candidates WHERE id="candidate-a"').fetchone())

    def receipt_count(self):
        with app.connection() as db:
            return db.execute('SELECT count(*) FROM task_write_receipts WHERE user_id="alice"').fetchone()[0]

    def test_stable_new_identity_and_lost_ack_do_not_repeat_or_overwrite_following_write(self):
        operation = self.review_operation(complete=True, completedAt='2026-10-22')
        status, first = self.resolve(operation)
        self.assertEqual(status, 200)
        self.assertEqual(first['operationId'], operation['id'])
        task = first['tasks']['value'][0]
        self.assertEqual(task['id'], operation['intent']['newTaskId'])
        self.assertEqual(task['createdAt'], operation['intent']['createdAt'])
        self.assertEqual(task['completedAt'], '2026-10-22')
        self.assertEqual(first['candidates'], [dict(id='candidate-a', state='applied', version=2, taskId=task['id'])])
        changed = dict(task, status='not_started', completedAt='', serverMetadata='retained')
        self.assertEqual(self.write(1, tasks=[changed])[0], 200)
        status, replay = self.resolve(operation, tasks_version=123)
        self.assertEqual(status, 200)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['tasks'], dict(version=2, value=[changed]))
        self.assertEqual(self.candidate_state(), dict(state='applied', version=2))
        status, recovery = self.call('/tasks/reconcile', dict(owner='alice', operations=[operation]))
        self.assertEqual(status, 200)
        self.assertEqual(recovery['applied'], [operation['id']])
        self.assertEqual(recovery['candidates'][0]['taskId'], task['id'])
        self.assertNotIn('candidate', recovery['candidates'][0])
        self.assertNotIn('batches', recovery)

    def test_ignore_reopen_ignore_receipts_survive_later_candidate_versions(self):
        ignore = self.review_operation(action='ignore')
        reopen = self.review_operation(oid='review-operation-0002', action='reopen', version=2)
        ignore_again = self.review_operation(oid='review-operation-0003', action='ignore', version=3)
        for operation in (ignore, reopen, ignore_again):
            self.assertEqual(self.resolve(operation)[0], 200)
        self.assertEqual(self.candidate_state(), dict(state='ignored', version=4))
        for operation in (ignore, reopen):
            status, replay = self.resolve(operation)
            self.assertEqual(status, 200)
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['candidates'], [dict(id='candidate-a', state='ignored', version=4)])
        self.assertEqual(self.candidate_state(), dict(state='ignored', version=4))
        self.assertEqual(self.receipt_count(), 3)

    def test_wrong_account_changed_identity_and_transport_mismatch_do_not_apply(self):
        operation = self.review_operation()
        self.assertEqual(self.resolve(operation, user='bob')[0], 409)
        self.assertEqual(self.resolve(operation, draft=dict(operation['intent']['draft'], title='其他内容'))[0], 400)
        self.assertEqual(self.resolve(operation, action='ignore')[0], 400)
        self.assertEqual(self.resolve(operation)[0], 200)
        changed = deepcopy(operation)
        changed['intent']['draft']['title'] = '改变相同编号的内容'
        self.assertEqual(self.resolve(changed)[0], 409)
        self.assertEqual(self.receipt_count(), 1)

    def test_another_operation_cannot_claim_a_previously_resolved_candidate(self):
        first = self.review_operation()
        self.assertEqual(self.resolve(first)[0], 200)
        second = self.review_operation(oid='different-review-0001', newTaskId='different-stable-task')
        self.assertEqual(self.resolve(second)[0], 409)
        self.assertEqual(self.receipt_count(), 1)

    def test_workspace_version_is_checked_but_not_part_of_the_stable_identity(self):
        operation = self.review_operation()
        self.assertEqual(self.resolve(operation, tasks_version=99)[0], 409)
        self.assertEqual(self.receipt_count(), 0)
        self.assertEqual(self.write()[0], 200)
        status, result = self.resolve(operation, tasks_version=1)
        self.assertEqual(status, 200)
        self.assertEqual(result['tasks']['version'], 2)
        self.assertEqual(len(result['tasks']['value']), 2)

    def test_target_content_changes_conflict_while_unrelated_metadata_is_preserved(self):
        self.assertEqual(self.write()[0], 200)
        with app.connection() as db:
            db.execute('UPDATE recognition_candidates SET candidate=? WHERE id="candidate-a"',
                       (json.dumps(dict(self.candidate, notice_type='update')),))
        expected = task_operations.target_snapshot(self.task)
        operation = self.review_operation(action='update', targetId='a', expectedTarget=expected)
        changed = dict(self.task, title='另一窗口新标题')
        other = dict(self.operation, id='other-write-00000001')
        self.assertEqual(self.write(1, operation=other, tasks=[changed])[0], 200)
        self.assertEqual(self.resolve(operation, tasks_version=2)[0], 409)
        self.assertEqual(self.candidate_state(), dict(state='pending', version=1))
        fresh = deepcopy(operation)
        fresh['intent']['expectedTarget'] = task_operations.target_snapshot(changed)
        metadata = dict(changed, unrelatedSourceMetadata={'kept': True})
        self.assertEqual(self.write(2, operation=dict(other, id='other-write-00000002'), tasks=[metadata])[0], 200)
        status, result = self.resolve(fresh, tasks_version=3)
        self.assertEqual(status, 200)
        self.assertEqual(result['tasks']['value'][0]['unrelatedSourceMetadata'], {'kept': True})

    def test_receipt_failure_rolls_back_both_candidate_and_task(self):
        operation = self.review_operation()
        with app.connection() as db:
            db.execute("CREATE TRIGGER reject_review_receipt BEFORE INSERT ON task_write_receipts BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.assertGreaterEqual(self.resolve(operation)[0], 500)
        self.assertEqual(self.candidate_state(), dict(state='pending', version=1))
        self.assertNotIn('tasks', self.call('/workspace')[1])
        with app.connection() as db:
            db.execute('DROP TRIGGER reject_review_receipt')
        self.assertEqual(self.resolve(operation)[0], 200)

    def test_clear_generation_rejects_unsent_review_but_replays_old_ack_without_resurrection(self):
        committed = self.review_operation()
        self.assertEqual(self.resolve(committed)[0], 200)
        with app.connection() as db:
            task_operations.invalidate_pending(db, 'alice')
            db.execute('DELETE FROM recognition_candidates WHERE user_id="alice"')
            db.execute('UPDATE workspace SET value="[]",version=2 WHERE user_id="alice" AND name="tasks"')
        unknown = deepcopy(committed); unknown['id'] = 'review-operation-unknown'
        self.assertEqual(self.resolve(unknown, tasks_version=2)[0], 409)
        status, replay = self.resolve(committed)
        self.assertEqual(status, 200)
        self.assertEqual(replay['tasks'], dict(value=[], version=2))
        self.assertEqual(replay['candidates'], [dict(id='candidate-a', state='missing', version=0)])
        self.assertEqual(replay['generation'], 1)

    def test_stable_task_collision_and_wrong_endpoint_never_mutate_candidate(self):
        operation = self.review_operation(newTaskId='stable-existing-task')
        self.assertEqual(self.write(tasks=[dict(self.task, id='stable-existing-task')])[0], 200)
        self.assertEqual(self.resolve(operation, tasks_version=1)[0], 409)
        self.assertEqual(self.write(1, operation=operation)[0], 400)
        self.assertEqual(self.candidate_state(), dict(state='pending', version=1))

    def test_review_contract_example_matches_identity_validation(self):
        from jsonschema import Draft202012Validator
        contracts = Path(__file__).resolve().parents[1] / 'contracts'
        schema = json.loads((contracts / 'schemas/task-writes-v0.1.schema.json').read_text())
        example = json.loads((contracts / 'examples/task-writes-v01.json').read_text())['reviewOperation']
        Draft202012Validator(schema).validate(example)
        task_operations.identity(example, example['owner'], app.ApiError)
        invalid = deepcopy(example); del invalid['intent']['newTaskId']
        self.assertFalse(Draft202012Validator(schema).is_valid(invalid))
        with self.assertRaises(app.ApiError):
            task_operations.identity(invalid, invalid['owner'], app.ApiError)

    def test_same_operation_identity_is_scoped_to_each_account(self):
        alice = self.review_operation()
        self.assertEqual(self.resolve(alice)[0], 200)
        with app.connection() as db:
            self.add_candidate(db, 'candidate-b', user='bob')
        bob = self.review_operation(cid='candidate-b')
        bob['owner'] = 'bob'
        status, result = self.resolve(bob, user='bob')
        self.assertEqual(status, 200)
        self.assertEqual(result['tasks']['value'][0]['id'], alice['intent']['newTaskId'])
        status, recovery = self.call('/tasks/reconcile', dict(owner='bob', operations=[bob]), user='bob')
        self.assertEqual(status, 200)
        self.assertEqual(recovery['applied'], [bob['id']])
        self.assertEqual([candidate['id'] for candidate in recovery['candidates']], ['candidate-b'])

    def test_candidate_version_and_foreign_candidate_are_checked_before_receipting(self):
        operation = self.review_operation(version=99)
        self.assertEqual(self.resolve(operation)[0], 409)
        foreign = self.review_operation(); foreign['owner'] = 'bob'
        self.assertEqual(self.resolve(foreign, user='bob')[0], 404)
        self.assertEqual(self.receipt_count(), 0)
        self.assertEqual(self.candidate_state(), dict(state='pending', version=1))

    def test_wrong_resolve_intent_and_invalid_transport_or_dates_are_rejected(self):
        self.assertEqual(self.call('/recognition/candidates/candidate-a/resolve',
            dict(action='ignore', version=1, operation=self.operation))[0], 400)
        self.assertEqual(self.resolve(self.review_operation(), tasks_version=True)[0], 400)
        self.assertEqual(self.resolve(self.review_operation(complete=True, completedAt='2026-02-30'))[0], 400)
        self.assertEqual(self.resolve(self.review_operation(createdAt='2026-10-23T10:00:00'))[0], 400)
        self.assertEqual(self.receipt_count(), 0)
        self.assertEqual(self.candidate_state(), dict(state='pending', version=1))

    def test_postpone_then_cancel_use_the_latest_workspace_and_explicit_target_values(self):
        self.assertEqual(self.write()[0], 200)
        with app.connection() as db:
            db.execute('UPDATE recognition_candidates SET candidate=? WHERE id="candidate-a"',
                       (json.dumps(dict(self.candidate, notice_type='postpone')),))
            self.add_candidate(db, 'candidate-cancel', kind='cancel')
        due = dict(date='2026-11-01', precision='date')
        draft = dict(title=self.task['title'], description='', course=None, content_type='task', due=due)
        postpone = self.review_operation(action='postpone', targetId='a',
            expectedTarget=task_operations.target_snapshot(self.task), draft=draft)
        status, result = self.resolve(postpone, tasks_version=1)
        self.assertEqual(status, 200)
        self.assertEqual(result['tasks']['version'], 2)
        target = result['tasks']['value'][0]
        self.assertEqual(target['due'], due)
        cancel = self.review_operation(oid='cancel-operation-0001', cid='candidate-cancel', action='cancel',
            targetId='a', expectedTarget=task_operations.target_snapshot(target))
        status, result = self.resolve(cancel, tasks_version=2)
        self.assertEqual(status, 200)
        self.assertEqual(result['tasks']['version'], 3)
        self.assertEqual(result['tasks']['value'][0]['status'], 'cancelled')
        self.assertEqual(result['tasks']['value'][0]['due'], due)
        self.assertEqual(self.resolve(cancel, tasks_version=0)[0], 200)

    def test_old_client_without_operation_keeps_its_response_and_idempotency(self):
        intent = self.review_operation()['intent']
        body = dict(action='create', version=1, tasks_version=0, reviewed=True, target_id=None, draft=intent['draft'])
        for _ in range(2):
            status, result = self.call('/recognition/candidates/candidate-a/resolve', body)
            self.assertEqual(status, 200)
            self.assertIn('batches', result)
            self.assertNotIn('operationId', result)
        self.assertEqual(self.candidate_state(), dict(state='applied', version=2))
        self.assertEqual(self.receipt_count(), 0)


if __name__ == '__main__':
    unittest.main()
