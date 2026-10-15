import json
import unittest
from copy import deepcopy
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError
from contracts import public_import as contract


class PublicImportContractTests(unittest.TestCase):
    def test_shared_envelopes_and_standalone_bundle(self):
        fixtures=json.loads((Path(__file__).parents[1]/'examples/public-import-v01.json').read_text(encoding='utf-8'))
        for kind,value in fixtures.items():
            contract.check(kind,value)
            Draft202012Validator(contract.bundle()).validate(value)

    def test_duplicate_events_wrong_source_and_terminal_mismatch_rejected(self):
        fixtures=json.loads((Path(__file__).parents[1]/'examples/public-import-v01.json').read_text(encoding='utf-8'))
        run=deepcopy(fixtures['run'])
        run['result']['events']*=2
        with self.assertRaises(ValueError):contract.check('run',run)
        run=deepcopy(fixtures['run']);run['source_id']='bio'
        with self.assertRaises(ValueError):contract.check('run',run)
        run=deepcopy(fixtures['run']);run['state']='running'
        with self.assertRaises(ValueError):contract.check('run',run)
        with self.assertRaises(ValidationError):contract.check('start',dict(fixtures['start'],source_id='http://127.0.0.1'))
