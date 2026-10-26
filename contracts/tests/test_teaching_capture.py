"""Standalone capture root and cross-field identity tests, no service or account."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, FormatChecker
from contracts.teaching_capture import SCHEMA, check_capture


class TeachingCaptureTests(unittest.TestCase):
    def capture(self,state='submitted'):
        cases=json.loads((Path(__file__).resolve().parents[1]/'examples/teaching-v04-cases.json').read_text(encoding='utf-8'))['cases']
        request=deepcopy(cases[0]['request']);request['records']=request['records'][:1]
        r=request['records'][0]['record']
        bases={'submitted':'review_submission_history_title','active':'active_assignment_page','unknown':'unknown'}
        return dict(contract_version='teaching-capture/0.1',request=request,assignment=dict(
            state=state,basis=bases[state],course_id=r['course_id'],content_id=r['source_item_id']))

    def test_root_is_standalone_for_all_states_and_non_native_sources(self):
        Draft202012Validator.check_schema(SCHEMA)
        for state in ('submitted','active','unknown',None):
            value=self.capture(state or 'unknown')
            if state is None:value['assignment']=None
            Draft202012Validator(SCHEMA,format_checker=FormatChecker()).validate(value)
            self.assertEqual(check_capture(value),value)

    def test_mismatched_identity_and_forged_basis_are_rejected(self):
        for key,value in [('course_id','other'),('content_id','other'),('basis','unknown')]:
            capture=self.capture();capture['assignment'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):check_capture(capture)

    def test_added_trust_fields_multi_record_and_invalid_dates_are_rejected(self):
        for mutation in ('trust','multi','date'):
            value=self.capture()
            if mutation=='trust':value['source_verified']=True
            elif mutation=='multi':value['request']['records']*=2
            else:value['request']['records'][0]['record']['collected_at']='not-a-date'
            with self.subTest(mutation=mutation),self.assertRaises(Exception):check_capture(value)
