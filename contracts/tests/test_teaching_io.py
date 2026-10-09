import json
from copy import deepcopy
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from contracts.build_teaching_v04 import schemas
from contracts.teaching_io import SCHEMAS, check_request, check_pair, snapshot_digest

ROOT=Path(__file__).resolve().parents[1]
CASES=json.loads((ROOT/'examples/teaching-v04-cases.json').read_text(encoding='utf-8'))['cases']


class TeachingIOTests(unittest.TestCase):
    def sample(self):
        case=deepcopy(CASES[0]);return case['request'],case['expected']

    def check(self,request,result,verified=True):
        return check_pair(request,result,verified_snapshot_digests={snapshot_digest(e) for e in request['records']} if verified else ())

    def test_standalone_schemas_and_shared_boundary_pairs(self):
        self.assertEqual(SCHEMAS,schemas())
        for value in SCHEMAS.values():Draft202012Validator.check_schema(value)
        for case in CASES:
            with self.subTest(name=case['name']):self.check(case['request'],case['expected'],case['verified'])

    def test_duplicates_identity_and_source_changes_rejected(self):
        for change in ('duplicate_input','wrong_input','snapshot','source','duplicate_candidate'):
            request,result=self.sample()
            if change=='duplicate_input':request['records']*=2
            elif change=='wrong_input':result['record_results'][0]['input_id']='different'
            elif change=='snapshot':result['record_results'][0]['snapshot_id']='different'
            elif change=='source':result['record_results'][0]['source']['label']='different'
            else:result['record_results'][0]['decisions']*=2
            with self.subTest(change=change),self.assertRaises(Exception):self.check(request,result)

    def test_partial_snapshot_cannot_claim_no_tasks_or_auto_task(self):
        request,result=self.sample();request['records'][0]['record']['body_status']='partial'
        row=result['record_results'][0];row['decisions'][0]['pair']['record']['body_status']='partial'
        with self.assertRaises(ValueError):self.check(request,result)
        row.update(decisions=[],status='no_tasks');result['status']='no_tasks'
        with self.assertRaises(ValueError):self.check(request,result)

    def test_json_trust_and_unverified_auto_recommendations_rejected(self):
        request,result=self.sample()
        with self.assertRaises(ValueError):self.check(request,result,False)
        request['records'][0]['source_verified']=True
        with self.assertRaises(Exception):check_request(request)

    def test_invalid_url_and_input_limits_rejected(self):
        for url in ('file:///private','https://user:password@example.com/path','http://127.0.0.1/data','https://example.com/?token=private'):
            request,_=self.sample();request['records'][0]['source']['url']=url
            with self.subTest(url=url),self.assertRaises(ValueError):check_request(request)
        request,_=self.sample();request['records'][0]['source']['label']='x'*513
        with self.assertRaises(Exception):check_request(request)

    def test_row_and_batch_statuses_are_consistent(self):
        for change in ('batch','row','error','missing','rewrite_body'):
            request,result=self.sample();row=result['record_results'][0]
            if change=='batch':result['status']='no_tasks'
            elif change=='row':row['status']='failed'
            elif change=='error':row['error']=dict(code='agent_timeout',message='timeout',retryable=True)
            elif change=='missing':result['record_results']=[]
            else:row['decisions'][0]['pair']['record']['text']+='forged'
            with self.subTest(change=change),self.assertRaises(ValueError):self.check(request,result)

    def test_snapshot_digest_covers_every_record_and_display_field(self):
        request,_=self.sample();original=request['records'][0];digest=snapshot_digest(original)
        for part,field in (('record','text'),('record','snapshot_id'),('record','source_metadata_text'),('source','course_or_project')):
            changed=deepcopy(original);changed[part][field]+='different'
            self.assertNotEqual(snapshot_digest(changed),digest)

    def test_unattempted_record_cannot_be_reported_as_success(self):
        request,result=self.sample();result['record_results'][0]['attempted']=False
        with self.assertRaises(ValueError):self.check(request,result)


if __name__=='__main__':unittest.main()
