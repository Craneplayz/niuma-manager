import copy
import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas/error-report-v0.1.schema.json').read_text())
CASES = json.loads((ROOT / 'examples/error-reports-v01.json').read_text())['reports']


class ErrorReportContractTests(unittest.TestCase):
    def test_shared_examples_are_valid(self):
        Draft202012Validator.check_schema(SCHEMA)
        validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
        for case in CASES:
            validator.validate(case)

    def test_private_or_unbounded_fields_are_rejected(self):
        validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
        for field, value in [('message', 'PRIVATE'), ('stack', 'PRIVATE'), ('url', 'https://private.test'), ('password', 'PRIVATE'), ('code', 'PRIVATE'), ('status', True), ('frontend_revision', 'PRIVATE'), ('occurred_at', 'yesterday')]:
            with self.subTest(field=field):
                case = copy.deepcopy(CASES[0])
                case[field] = value
                self.assertFalse(validator.is_valid(case))
