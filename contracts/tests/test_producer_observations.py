import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas/producer-observation-v0.1.schema.json').read_text())
EXAMPLES = json.loads((ROOT / 'examples/producer-observations-v01.json').read_text())


class ProducerObservationContractTests(unittest.TestCase):
    def test_shared_examples(self):
        Draft202012Validator.check_schema(SCHEMA)
        for example in EXAMPLES:
            Draft202012Validator(SCHEMA).validate(example)

    def test_private_fields_and_unbounded_values_are_rejected(self):
        validator = Draft202012Validator(SCHEMA)
        for field in ('url', 'account', 'cookie', 'api_key', 'prompt', 'content', 'response', 'request_id'):
            event = dict(EXAMPLES[2], **{field: 'PRIVATE'})
            self.assertFalse(validator.is_valid(event), field)
        for field in ('input_tokens', 'output_tokens', 'total_tokens'):
            for value in (-1, True, 1.5, '12', 9007199254740992):
                event = dict(EXAMPLES[2], **{field: value})
                self.assertFalse(validator.is_valid(event), (field, value))
        event = copy.deepcopy(EXAMPLES[2])
        event['result_code'] = 'PRIVATE_PROVIDER_MESSAGE'
        self.assertFalse(validator.is_valid(event))
