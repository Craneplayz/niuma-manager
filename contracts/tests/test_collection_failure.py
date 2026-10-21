import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator
from contracts.collection_failure import SCHEMA, parse_failure

EXAMPLES = json.loads((Path(__file__).resolve().parents[1] / 'examples/collection-failures-v01.json').read_text())


class CollectionFailureContractTests(unittest.TestCase):
    def test_examples_and_unknown_legacy_output(self):
        Draft202012Validator.check_schema(SCHEMA)
        for event in EXAMPLES:
            Draft202012Validator(SCHEMA).validate(event)
            self.assertEqual(parse_failure('untrusted legacy stderr\n' + json.dumps(event)), event)
        for value in ('legacy text PRIVATE', None, 'x' * 16385, '[]', '{"stage":', '[' * 1200):
            self.assertIsNone(parse_failure(value))

    def test_no_extra_private_fields_or_arbitrary_codes(self):
        for field in ('url', 'account', 'body', 'cookie', 'password', 'stack'):
            event = dict(EXAMPLES[0], **{field: 'PRIVATE'})
            self.assertIsNone(parse_failure(json.dumps(event)))
            self.assertFalse(Draft202012Validator(SCHEMA).is_valid(event))
        for key in ('stage', 'code', 'contract_version'):
            self.assertIsNone(parse_failure(json.dumps(dict(EXAMPLES[0], **{key: 'PRIVATE'}))))
