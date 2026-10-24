"""Shared bulk actions keep frontend envelopes and service validation aligned."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from contracts import task_bulk


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = json.loads((ROOT / 'examples/task-bulk-v01.json').read_text(encoding='utf-8'))


class TaskBulkContractTests(unittest.TestCase):
    def test_generated_self_contained_schema_and_shared_examples(self):
        schema = json.loads((ROOT / 'schemas/task-bulk-v0.1.schema.json').read_text(encoding='utf-8'))
        self.assertEqual(schema, task_bulk.bundle())
        Draft202012Validator.check_schema(schema)
        document = Draft202012Validator(schema, format_checker=FormatChecker())
        for kind, examples in EXAMPLES.items():
            for example in examples:
                with self.subTest(kind=kind):
                    task_bulk.check(kind, example)
                    document.validate(example)

    def test_task_write_recovery_schema_accepts_same_bulk_intent(self):
        schema = json.loads((ROOT / 'schemas/task-writes-v0.1.schema.json').read_text(encoding='utf-8'))
        self.assertEqual(schema['$defs']['bulkIntent'], task_bulk.INTENT)
        operation = EXAMPLES['request'][0]['operation']
        Draft202012Validator(schema).validate(operation)
        Draft202012Validator(schema).validate(dict(owner=operation['owner'], operations=[operation]))
        self.assertEqual(schema['$defs']['bulkUndo'], task_bulk.UNDO)
        recovery = dict(applied=[operation['id']], tasks=dict(value=[], version=2), candidates=[], generation=0,
                        bulkResults=[dict(operationId=operation['id'], undo=EXAMPLES['request'][1]['operation']['intent'])])
        Draft202012Validator(schema).validate(recovery)

    def test_no_ambiguous_target_duplicates_or_invalid_dates(self):
        original = EXAMPLES['request'][0]
        for invalid in ('empty', 'duplicate_task', 'duplicate_candidate', 'overlap_id', 'missing_date', 'invalid_date', 'candidate_reopen', 'time_without_day'):
            example = deepcopy(original)
            intent = example['operation']['intent']
            if invalid == 'empty': intent.update(tasks=[], candidates=[])
            elif invalid == 'duplicate_task': intent['tasks'] *= 2
            elif invalid == 'duplicate_candidate': intent['candidates'] *= 2
            elif invalid == 'overlap_id': intent['tasks'][0]['id'] = intent['candidates'][0]['taskId']
            elif invalid == 'missing_date': intent.pop('date')
            elif invalid == 'invalid_date': intent['date'] = '2026-02-30'
            elif invalid == 'candidate_reopen': intent['action'] = 'reopen'
            elif invalid == 'time_without_day': intent['tasks'][0]['expected']['dueTime'] = '18:30'
            with self.subTest(invalid=invalid), self.assertRaises((ValueError, ValidationError)):
                task_bulk.check('request', example)

    def test_undo_references_original_operation_without_accepting_client_inverse_fields(self):
        original = EXAMPLES['request'][1]
        task_bulk.check('request', original)
        for field in ('tasks', 'candidates', 'previous', 'date'):
            example = deepcopy(original)
            example['operation']['intent'][field] = []
            with self.subTest(field=field), self.assertRaises(ValidationError):
                task_bulk.check('request', example)


if __name__ == '__main__':
    unittest.main()
