from copy import deepcopy
import json
import unittest

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from contracts import submission_operations as public
from contracts import submission_policy as policy

DATA = json.loads((public.ROOT / 'examples/submission-operations-v01-cases.json').read_text(encoding='utf-8'))


class SubmissionOperationTests(unittest.TestCase):
    def sample(self, index=0):
        case = deepcopy(DATA['cases'][index])
        return case['record'], case['operation']

    def test_self_contained_schema_and_shared_examples(self):
        schema = json.loads((public.ROOT / 'schemas/submission-operations-v0.1.schema.json').read_text(encoding='utf-8'))
        self.assertEqual(schema, public.schema())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for kind in ('directory', 'receipt', 'error'):
            validator.validate(DATA[kind])
            public.check(kind, DATA[kind])
        for case in DATA['cases']:
            with self.subTest(case=case['name']):
                before = deepcopy(case['record'])
                validator.validate(before)
                validator.validate(case['operation'])
                result = public.apply_operation(before, case['operation'])
                self.assertEqual(result['record']['observation'], case['expected_observation'])
                self.assertEqual(before, case['record'], 'helper mutated caller state')
                self.assertEqual(result['audit']['before'], before)
                self.assertEqual(result['audit']['after'], result['record'])
                self.assertEqual(result['audit']['operation'], case['operation'])
                for key in ('record_id', 'label', 'course_or_project', 'action_labels'):
                    self.assertEqual(result['record'][key], before[key])
                public.check('receipt', result['receipt'])

    def test_complete_mapping_never_swallows_another_action(self):
        for mutate in (lambda links: links.pop(), lambda links: links.append(deepcopy(links[0])),
                       lambda links: links[0].update(action_id='task:other-account')):
            record, operation = self.sample()
            mutate(operation['links'])
            with self.assertRaises((ValueError, ValidationError)):
                public.apply_operation(record, operation)

    def test_submission_only_rejects_mixed_and_unknown_relations(self):
        record, operation = self.sample()
        operation['scope'] = 'submission_only'
        with self.assertRaises(ValueError):
            public.apply_operation(record, operation)
        operation['links'][1]['relation'] = 'unknown'
        with self.assertRaises(ValueError):
            public.apply_operation(record, operation)

    def test_foreign_record_and_stale_snapshots_or_controls(self):
        for key, value in (('record_id', 'record:elsewhere'), ('expected_snapshot_id', 'snapshot:old'),
                           ('expected_control_revision', 6)):
            record, operation = self.sample()
            operation[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                public.apply_operation(record, operation)
        record, operation = self.sample()
        after = public.apply_operation(record, operation)['record']
        competing = dict(operation, operation_id='operation:competing')
        with self.assertRaises(public.OperationConflict):
            public.apply_operation(after, competing)

    def test_idempotency_digest_binds_every_operation_field(self):
        _, operation = self.sample()
        original = public.request_digest(operation)
        self.assertEqual(original, public.request_digest(dict(reversed(list(operation.items())))))
        for key, value in (('record_id', 'record:other'), ('expected_snapshot_id', 'snapshot:9'),
                           ('expected_control_revision', 8), ('scope', 'unknown')):
            changed = dict(operation, **{key: value})
            self.assertNotEqual(original, public.request_digest(changed))
        changed = deepcopy(operation)
        changed['links'][0]['relation'] = 'other'
        self.assertNotEqual(original, public.request_digest(changed))

    def test_client_cannot_supply_source_facts_or_decisions(self):
        record, operation = self.sample()
        for field, value in (('state', 'submitted'), ('basis', 'review_submission_history_title'),
                             ('verified_digest', 'a'*64), ('actor', 'other-user'), ('observation', record['observation']),
                             ('sequence', 999), ('completion_origin', 'source_submission')):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                public.apply_operation(record, dict(operation, **{field: value}))
        operation['links'][0]['link_basis'] = 'server_exact'
        with self.assertRaises(ValidationError):
            public.apply_operation(record, operation)

    def test_association_preserves_decisions_and_source_facts(self):
        for decision, origin in (('ignored', None), ('deleted', None), ('completed', 'manual'), ('edited', None)):
            record, operation = self.sample()
            record['observation']['review_pending'] = True
            record['observation']['actions'][0].update(decision=decision, completion_origin=origin)
            after = public.apply_operation(record, operation)['record']['observation']
            self.assertEqual(after['snapshot'], record['observation']['snapshot'])
            self.assertTrue(after['review_pending'])
            self.assertEqual(after['actions'][0]['decision'], decision)
            self.assertEqual(after['actions'][0]['completion_origin'], origin)
            self.assertEqual(after['actions'][1], record['observation']['actions'][1])

    def test_association_cannot_silently_unpause(self):
        paused, _ = self.sample(2)
        _, operation = self.sample()
        for relation in ('other', 'unknown'):
            operation['links'][0]['relation'] = relation
            with self.assertRaises(ValueError):
                public.apply_operation(paused, operation)

    def test_paused_candidate_allows_audited_relationship_correction(self):
        paused,_=self.sample(2)
        paused['observation']['actions'][0].update(kind='candidate',decision='pending')
        _,template=self.sample()
        for relation in ('assignment_submission','other','unknown'):
            operation=deepcopy(template);operation['links'][0]['relation']=relation
            result=public.apply_operation(paused,operation)
            after=result['record']['observation']
            action=after['actions'][0]
            self.assertEqual(action['relation'],relation)
            self.assertEqual(action['source_completion_paused'],relation=='assignment_submission')
            self.assertEqual(action['decision'],'pending')
            self.assertEqual(after['review_pending'],paused['observation']['review_pending'])
            self.assertEqual(result['audit']['before'],paused)
            self.assertEqual(result['audit']['after'],result['record'])

    def test_unknown_source_is_not_promoted_by_confirmation(self):
        record, operation = self.sample(3)
        record['observation']['snapshot'].update(state='unknown', basis='unknown')
        after = public.apply_operation(record, operation)['record']['observation']
        self.assertEqual(after['snapshot']['state'], 'unknown')
        self.assertTrue(after['actions'][0]['source_completion_paused'])

    def test_control_operations_reuse_internal_semantics(self):
        for index in (1, 2, 3):
            record, operation = self.sample(index)
            internal = {k: v for k, v in operation.items() if k != 'record_id'}
            internal['contract_version'] = policy.VERSION
            result = public.apply_operation(record, operation)
            self.assertEqual(result['record']['observation'], policy.apply_user_control(record['observation'], internal)['observation'])
        for identity in ('task:other-account', 'task:prepare'):
            record, operation = self.sample(1)
            operation['action_ids'] = [identity]
            with self.assertRaises(ValueError):
                public.apply_operation(record, operation)
        record, operation = self.sample(3)
        record['observation']['review_pending'] = False
        with self.assertRaises(ValueError):
            public.apply_operation(record, operation)

    def test_action_labels_must_describe_exact_record(self):
        for change in (lambda items: items.pop(), lambda items: items.append(deepcopy(items[0])),
                       lambda items: items[0].update(action_id='action:elsewhere')):
            record, _ = self.sample()
            change(record['action_labels'])
            with self.assertRaises(ValueError):
                public.check('detail', record)

    def test_receipts_cannot_claim_another_record_or_version(self):
        for key, value in (('record_id', 'other'), ('applied_control_revision', 100)):
            receipt = dict(deepcopy(DATA['receipt']), **{key: value})
            with self.assertRaises(ValueError):
                public.check('receipt', receipt)

    def test_legacy_input_identity_and_payload_limits(self):
        record, operation = self.sample()
        identity = ' 教学网/中文/作业 2 ' + 'x'*1000
        record['observation']['snapshot']['input_id'] = identity
        after = public.apply_operation(record, operation)['record']
        self.assertEqual(after['observation']['snapshot']['input_id'], identity)
        record['observation']['snapshot']['input_id'] = 'x'*200_001
        with self.assertRaises(ValueError):
            public.check('detail', record)
        record, operation = self.sample()
        record['label'] = 'x'*513
        with self.assertRaises(ValidationError):
            public.check('detail', record)
        record['label'] = 'bad\x00label'
        with self.assertRaises(ValidationError):
            public.check('detail', record)
        operation['links'] *= 51
        with self.assertRaises(ValidationError):
            public.check('operation', operation)

    def test_directory_is_unique_and_empty_selection_rules(self):
        directory = deepcopy(DATA['directory'])
        directory['records'] *= 2
        with self.assertRaises(ValueError):
            public.check('directory', directory)
        record, operation = self.sample(1)
        operation['action_ids'] = []
        with self.assertRaises(ValueError):
            public.apply_operation(record, operation)
        record, operation = self.sample(3)
        operation['action_ids'] = ['task:submit']
        with self.assertRaises(ValueError):
            public.apply_operation(record, operation)


if __name__ == '__main__':
    unittest.main()
