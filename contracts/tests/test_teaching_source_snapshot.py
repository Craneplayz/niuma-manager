"""Validate synthetic #107 source snapshots, without network/model access."""
import copy
import json
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator
from ddl_text.contract_v02 import validate_request

ROOT = Path(__file__).resolve().parents[1]

class TeachingSourceSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / 'schemas/teaching-source-snapshot-v0.2.schema.json').read_text())
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)
        cls.cases = json.loads((ROOT / 'source-to-text/teaching-source-snapshot-v02-cases.json').read_text())['cases']

    def test_emitted_requests_and_reading_metadata(self):
        for case in self.cases:
            with self.subTest(case=case['id']):
                validate_request(case['request'])
                record = case['request']['records'][0]
                self.assertEqual(record['text_quality'], case['expected_text_quality'])
                self.validator.validate(record['upstream_hints'])

    def test_partial_reference_cannot_claim_read(self):
        hints = copy.deepcopy(self.cases[1]['request']['records'][0]['upstream_hints'])
        hints['references'][0].update(read_status='partial', content_read=True)
        self.assertTrue(list(self.validator.iter_errors(hints)))

    def test_complete_reference_cannot_retain_failure_reason(self):
        case = next(case for case in self.cases if case['id'] == 'text-attachment-read')
        hints = copy.deepcopy(case['request']['records'][0]['upstream_hints'])
        hints['references'][0]['reason'] = 'download_failed'
        self.assertTrue(list(self.validator.iter_errors(hints)))
