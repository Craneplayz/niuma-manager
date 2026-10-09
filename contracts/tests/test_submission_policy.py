from copy import deepcopy
import json
import unittest
from jsonschema import Draft202012Validator
from contracts.submission_policy import ROOT,schema,check,decide,observation_digest,check_successor

CASES=json.loads((ROOT/'examples/submission-policy-v01-cases.json').read_text(encoding='utf-8'))['cases']


class SubmissionPolicyTests(unittest.TestCase):
    def sample(self,index=1):return deepcopy(CASES[index]['observation'])

    def evaluate(self,source,**kwargs):
        return decide(source,verified_digest=observation_digest(source),source_account_id=source['snapshot']['source_account_id'],
            input_id=source['snapshot']['input_id'],**kwargs)

    def test_schema_and_shared_decisions(self):
        self.assertEqual(json.loads((ROOT/'schemas/submission-policy-v0.1.schema.json').read_text(encoding='utf-8')),schema())
        Draft202012Validator.check_schema(schema())
        for case in CASES:
            source=case['observation']
            result=decide(source,verified_digest=observation_digest(source) if case['verified'] else None,
                source_account_id=source['snapshot']['source_account_id'],input_id=source['snapshot']['input_id'],
                latest_sequence=case['latest_sequence'],previous_state=case['previous_state'])
            self.assertEqual(result,case['expected'],case['name'])

    def test_default_import_and_wrong_source_never_complete(self):
        source=self.sample()
        for kwargs in ({},{'verified_digest':observation_digest(source),'source_account_id':'other','input_id':'item:a'},
                       {'verified_digest':observation_digest(source),'source_account_id':'source:a','input_id':'other'}):
            result=decide(source,**kwargs)
            self.assertEqual(result['disposition'],'untrusted');self.assertFalse(result['complete_action_ids'])

    def test_changed_state_or_action_binding_breaks_verification(self):
        for part in ('snapshot','actions','scope'):
            source=self.sample();digest=observation_digest(source)
            if part=='snapshot':source['snapshot']['snapshot_id']='new'
            elif part=='actions':source['actions'][1]['action_id']='other'
            else:source['scope']='mixed'
            result=decide(source,verified_digest=digest,source_account_id='source:a',input_id='item:a')
            self.assertFalse(result['complete_action_ids'])

    def test_no_model_or_file_dependency_is_required_for_submitted(self):
        source=self.sample(0)
        result=self.evaluate(source)
        self.assertTrue(result['skip_model'])
        self.assertFalse(result['needs_review'])

    def test_unknown_or_unrelated_action_never_completes(self):
        for relation in ('other','unknown'):
            source=self.sample();source['scope']='mixed'
            for action in source['actions']:
                action.update(relation=relation,link_basis='unresolved' if relation=='unknown' else 'user_confirmed')
            result=self.evaluate(source)
            self.assertFalse(result['complete_action_ids']);self.assertFalse(result['withdraw_candidate_ids'])

    def test_ignored_deleted_and_manual_completed_remain_untouched(self):
        for decision in ('ignored','deleted','completed'):
            source=self.sample();source['actions']=source['actions'][1:2]
            source['actions'][0].update(decision=decision,completion_origin='manual' if decision=='completed' else None)
            result=self.evaluate(source)
            self.assertFalse(result['complete_action_ids']);self.assertEqual(result['preserve_action_ids'],['task:1'])

    def test_schema_rejects_false_submission_or_invalid_mapping(self):
        for change in ('proof','duplicate','scope','origin','relation','field'):
            source=self.sample()
            if change=='proof':source['snapshot']['basis']='unknown'
            elif change=='duplicate':source['actions']*=2
            elif change=='scope':source['actions'][0]['relation']='other'
            elif change=='origin':source['actions'][1]['completion_origin']='manual'
            elif change=='relation':source['actions'][0]['link_basis']='unresolved'
            else:source['source_verified']=True
            with self.subTest(change=change),self.assertRaises(Exception):check(source)

    def test_newer_snapshot_same_assignment_and_order_required(self):
        before=self.sample();after=deepcopy(before);after['snapshot'].update(sequence=3,snapshot_id='snapshot:3')
        check_successor(before,after)
        for field in ('source_account_id','input_id','course_id','content_id'):
            changed=deepcopy(after);changed['snapshot'][field]='other'
            with self.subTest(field=field),self.assertRaises(ValueError):check_successor(before,changed)
        with self.assertRaises(ValueError):check_successor(before,before)
        changed=deepcopy(after);changed['snapshot']['observed_at']='2026-10-08T09:00:00+08:00'
        with self.assertRaises(ValueError):check_successor(before,changed)


if __name__=='__main__':unittest.main()
