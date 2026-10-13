import json
import unittest
from copy import deepcopy
from pathlib import Path
from jsonschema import Draft202012Validator
from contracts.source_sync import check, root_schema, SCHEMAS

EXAMPLES=json.loads((Path(__file__).parents[1]/'examples/source-sync-v01.json').read_text(encoding='utf-8'))


class SourceSyncContractTests(unittest.TestCase):
    def test_examples_and_independent_roots(self):
        for kind in SCHEMAS:Draft202012Validator.check_schema(root_schema(kind))
        for kind,value in EXAMPLES.items():check(kind,value)

    def test_duplicate_ids_incorrect_counts_and_live_records_in_terminal_run_rejected(self):
        for edit in ('duplicate','count','live'):
            value=deepcopy(EXAMPLES['run'])
            if edit=='duplicate':value['records'][1]['request_id']=value['records'][0]['request_id']
            if edit=='count':value['counts']['total']=1
            if edit=='live':
                value['records'][1]['state']='running';value['counts'].update(incomplete=0,running=1)
            with self.assertRaises(ValueError):check('run',value)


if __name__=='__main__':unittest.main()
