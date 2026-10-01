import copy
import json
import unittest
from pathlib import Path
from text_contract import validate_request,validate_result

class TextContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture=json.loads((Path(__file__).parent.parent/'contracts/text-to-app/text-contract-v0.2.json').read_text(encoding='utf-8'))
        self.request=self.fixture['request'];self.result=self.fixture['expected']
    def test_example_and_no_mutation(self):
        before=copy.deepcopy(self.result)
        self.assertEqual(validate_result(self.request,self.result),before)
        self.assertEqual(self.result,before)
    def test_manual_platform_is_valid_and_preserves_candidate_sources(self):
        for record in self.request['records']: record['platform']='manual'
        for candidate in self.result['tasks']:
            for source in candidate['sources']: source['platform']='manual'
        validate_request(self.request)
        self.assertEqual(validate_result(self.request,self.result)['tasks'][0]['sources'][0]['platform'],'manual')
    def test_bad_evidence_fails_whole_record_not_other_records(self):
        self.result['tasks'][0]['evidence'][0]['quote']='这段原文从未出现'
        out=validate_result(self.request,self.result)
        self.assertEqual(out['status'],'partial_success')
        self.assertEqual(len(out['tasks']),1)
        self.assertEqual(out['record_results'][0]['status'],'failed')
    def test_reference_requires_review(self):
        c=self.result['tasks'][0]
        c['deadline'].update(kind='reference',status='inferred')
        self.assertEqual(validate_result(self.request,self.result)['record_results'][0]['status'],'failed')
        c.update(needs_review=True,review_reasons=['这是参考时间'])
        self.assertEqual(validate_result(self.request,self.result)['record_results'][0]['status'],'success')
    def test_conflict_cannot_have_chosen_value(self):
        self.result['tasks'][0]['deadline']['status']='conflict'
        self.assertEqual(validate_result(self.request,self.result)['record_results'][0]['status'],'failed')
    def test_empty_batch(self):
        self.request['records']=[]
        self.result.update(status='no_tasks',record_results=[],tasks=[],errors=[])
        self.assertEqual(validate_result(self.request,self.result)['status'],'no_tasks')
    def test_invalid_version_and_duplicate_input(self):
        self.request['contract_version']='0.1'
        with self.assertRaises(ValueError):validate_request(self.request)
        self.request['contract_version']='0.2'
        self.request['records'].append(self.request['records'][0])
        with self.assertRaises(ValueError):validate_request(self.request)
    def test_bad_source_and_supports(self):
        self.result['tasks'][0]['sources'][0]['url']='https://fabricated.test'
        self.assertEqual(validate_result(self.request,self.result)['record_results'][0]['status'],'failed')
    def test_inconsistent_aggregate_and_ids(self):
        self.result['status']='success'
        with self.assertRaises(ValueError):validate_result(self.request,self.result)
        self.result['status']='partial_success';self.result['record_results'][0]['task_ids']=['missing']
        with self.assertRaises(ValueError):validate_result(self.request,self.result)
    def test_partial_text_cannot_claim_no_deadline(self):
        self.request['records'][1]['text_quality']='summary'
        self.assertEqual(validate_result(self.request,self.result)['record_results'][1]['status'],'failed')
    def test_raw_time_cannot_be_invented(self):
        self.result['tasks'][0]['deadline']['raw']='2050年新截止日期'
        self.assertEqual(validate_result(self.request,self.result)['record_results'][0]['status'],'failed')
