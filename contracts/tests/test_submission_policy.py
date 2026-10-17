from copy import deepcopy
import json
import unittest
from jsonschema import Draft202012Validator
from contracts.submission_policy import (ROOT,schema,control_schema,check,decide,observation_digest,
    check_successor,apply_user_control)

CASES=json.loads((ROOT/'examples/submission-policy-v01-cases.json').read_text(encoding='utf-8'))['cases']


class SubmissionPolicyTests(unittest.TestCase):
    def sample(self,index=1):return deepcopy(CASES[index]['observation'])

    def evaluate(self,source,**kwargs):
        return decide(source,verified_digest=observation_digest(source),source_account_id=source['snapshot']['source_account_id'],
            input_id=source['snapshot']['input_id'],**kwargs)

    def operation(self,source,kind,action_ids=()):
        return dict(contract_version='submission-policy/0.1',operation_id='operation:user:1',
            expected_snapshot_id=source['snapshot']['snapshot_id'],expected_control_revision=source['control_revision'],
            kind=kind,action_ids=list(action_ids))

    def next_observation(self,source,state=None):
        after=deepcopy(source);snapshot=after['snapshot'];snapshot['sequence']+=1
        snapshot['snapshot_id']='snapshot:'+str(snapshot['sequence'])
        if state is not None:
            snapshot['state']=state
            snapshot['basis']={'submitted':'review_submission_history_title','active':'active_assignment_page','unknown':'unknown'}[state]
        check_successor(source,after)
        return after

    def test_schema_and_shared_decisions(self):
        self.assertEqual(json.loads((ROOT/'schemas/submission-policy-v0.1.schema.json').read_text(encoding='utf-8')),schema())
        Draft202012Validator.check_schema(schema())
        Draft202012Validator.check_schema(control_schema())
        self.assertEqual(json.loads((ROOT/'schemas/submission-control-v0.1.schema.json').read_text(encoding='utf-8')),control_schema())
        for case in CASES:
            source=case['observation']
            result=decide(source,verified_digest=observation_digest(source) if case['verified'] else None,
                source_account_id=source['snapshot']['source_account_id'],input_id=source['snapshot']['input_id'],
                latest_sequence=case['latest_sequence'],previous_state=case['previous_state'])
            self.assertEqual(result,case['expected'],case['name'])
        controls=json.loads((ROOT/'examples/submission-policy-v01-cases.json').read_text(encoding='utf-8'))['control_cases']
        for case in controls:
            result=apply_user_control(case['observation'],case['operation'])
            self.assertEqual(result['observation'],case['expected_observation'],case['name'])

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

    def test_auto_complete_then_manual_reopen_survives_new_submitted_snapshots(self):
        source=self.sample();source['actions']=source['actions'][1:2]
        self.assertEqual(self.evaluate(source)['complete_action_ids'],['task:1'])
        source['actions'][0].update(decision='completed',completion_origin='source_submission')
        opened=apply_user_control(source,self.operation(source,'reopen_actions',['task:1']))
        source=opened['observation']
        self.assertIsNone(source['actions'][0]['completion_origin'])
        self.assertEqual(source['actions'][0]['decision'],'confirmed')
        self.assertTrue(source['actions'][0]['source_completion_paused'])
        self.assertEqual(opened['audit']['actions_before'][0]['decision'],'completed')
        self.assertEqual(opened['audit']['to_revision'],opened['audit']['from_revision']+1)
        for _ in range(3):
            previous=source;source=self.next_observation(json.loads(json.dumps(source)))
            result=self.evaluate(source,latest_sequence=previous['snapshot']['sequence'],previous_state='submitted')
            self.assertEqual(result['complete_action_ids'],[])
            self.assertEqual(result['preserve_action_ids'],['task:1'])
            self.assertTrue(result['skip_model']);self.assertFalse(result['needs_review'])
        source=apply_user_control(source,self.operation(source,'resume_source_completion',['task:1']))['observation']
        source=self.next_observation(source)
        self.assertEqual(self.evaluate(source,previous_state='submitted')['complete_action_ids'],['task:1'])

    def test_pending_source_review_survives_repeated_and_recovered_observations(self):
        for state in ('active','unknown'):
            source=self.sample(5 if state=='active' else 6)
            first=self.evaluate(source,latest_sequence=1,previous_state='submitted')
            self.assertTrue(first['review_pending'])
            source.update(review_pending=first['review_pending'],control_revision=first['control_revision'])
            for next_state in (state,state,'submitted',state):
                previous=source;source=self.next_observation(json.loads(json.dumps(source)),next_state)
                result=self.evaluate(source,latest_sequence=previous['snapshot']['sequence'],previous_state=previous['snapshot']['state'])
                self.assertTrue(result['needs_review']);self.assertTrue(result['skip_model'])
                self.assertTrue(result['review_pending']);self.assertFalse(result['complete_action_ids'])
                self.assertFalse(result['withdraw_candidate_ids'])
                self.assertEqual(result['control_revision'],first['control_revision'])
            resolved=apply_user_control(source,self.operation(source,'resolve_source_review'))
            self.assertTrue(resolved['audit']['review_pending_before'])
            self.assertFalse(resolved['audit']['review_pending_after'])
            previous=resolved['observation'];source=self.next_observation(previous,state)
            result=self.evaluate(source,latest_sequence=previous['snapshot']['sequence'],previous_state=state)
            self.assertFalse(result['review_pending']);self.assertFalse(result['needs_review']);self.assertFalse(result['skip_model'])

    def test_stale_and_untrusted_inputs_cannot_clear_the_review_latch(self):
        source=self.sample();source.update(review_pending=True,control_revision=3)
        for result in (self.evaluate(source,latest_sequence=source['snapshot']['sequence']),decide(source)):
            self.assertTrue(result['review_pending']);self.assertTrue(result['needs_review']);self.assertTrue(result['skip_model'])
            self.assertEqual(result['control_revision'],3);self.assertFalse(result['complete_action_ids'])

    def test_user_pause_is_independent_of_review_resolution_and_other_tasks(self):
        source=self.sample();source['actions'][1]['source_completion_paused']=True
        result=self.evaluate(source)
        self.assertEqual(result['complete_action_ids'],['task:edited'])
        self.assertEqual(result['withdraw_candidate_ids'],['candidate:1'])
        self.assertEqual(result['preserve_action_ids'],['task:1'])
        source['review_pending']=True
        resolved=apply_user_control(source,self.operation(source,'resolve_source_review'))['observation']
        self.assertTrue(resolved['actions'][1]['source_completion_paused'])
        self.assertNotIn('task:1',self.evaluate(self.next_observation(resolved))['complete_action_ids'])
        resumed=apply_user_control(source,self.operation(source,'resume_source_completion',['task:1']))['observation']
        self.assertTrue(resumed['review_pending'])
        self.assertFalse(self.evaluate(self.next_observation(resumed))['complete_action_ids'])

    def test_controls_reject_stale_snapshot_revision_or_invalid_action_scope(self):
        source=self.sample();source['review_pending']=True
        for change in ('snapshot','revision','foreign_action','candidate','empty','duplicate','unpaused','ignored','other','review_targets'):
            value=deepcopy(source);operation=self.operation(value,'reopen_actions',['task:1'])
            if change=='snapshot':operation['expected_snapshot_id']='snapshot:old'
            elif change=='revision':operation['expected_control_revision']+=1
            elif change=='foreign_action':operation['action_ids']=['task:foreign']
            elif change=='candidate':operation['action_ids']=['candidate:1']
            elif change=='empty':operation['action_ids']=[]
            elif change=='duplicate':operation['action_ids']*=2
            elif change=='unpaused':operation['kind']='resume_source_completion'
            elif change=='ignored':value['actions'][1]['decision']='ignored'
            elif change=='other':value['scope']='mixed';value['actions'][1]['relation']='other'
            else:operation['kind']='resolve_source_review'
            with self.subTest(change=change),self.assertRaises(Exception):apply_user_control(value,operation)
        operation=self.operation(source,'resolve_source_review');after=apply_user_control(source,operation)['observation']
        with self.assertRaises(ValueError):apply_user_control(after,operation)
        self.assertTrue(source['review_pending'])  # Pure helper never mutates stored input.

    def test_controls_preserve_manual_completion_until_explicitly_reopened(self):
        source=self.sample();source['actions']=source['actions'][1:2]
        source['actions'][0].update(decision='completed',completion_origin='manual')
        self.assertFalse(self.evaluate(source)['complete_action_ids'])
        source=apply_user_control(source,self.operation(source,'reopen_actions',['task:1']))['observation']
        self.assertFalse(self.evaluate(self.next_observation(source))['complete_action_ids'])
        source['actions'][0]['decision']='edited'
        after=apply_user_control(source,self.operation(source,'resume_source_completion',['task:1']))['observation']
        self.assertEqual(after['actions'][0]['decision'],'edited')

    def test_durable_control_fields_are_required_and_covered_by_digest(self):
        for part in ('review_pending','control_revision','source_completion_paused'):
            source=self.sample();digest=observation_digest(source)
            if part=='source_completion_paused':source['actions'][1][part]=True
            elif part=='control_revision':source[part]+=1
            else:source[part]=True
            result=decide(source,verified_digest=digest,source_account_id='source:a',input_id='item:a')
            self.assertEqual(result['disposition'],'untrusted')
            if part=='source_completion_paused':del source['actions'][1][part]
            else:del source[part]
            with self.assertRaises(Exception):check(source)
        source=self.sample();source['actions'][0]['source_completion_paused']=True
        self.assertNotIn(source['actions'][0]['action_id'],self.evaluate(source)['withdraw_candidate_ids'])
        source['actions'][0].update(relation='other',link_basis='user_confirmed');source['scope']='mixed'
        with self.assertRaises(ValueError):check(source)

    def test_reopened_candidate_pause_preserves_only_that_action(self):
        source=self.sample()
        candidate=next(a for a in source['actions'] if a['kind']=='candidate')
        source['actions'].append(dict(candidate,action_id='candidate:unhandled'))
        candidate['source_completion_paused']=True
        result=self.evaluate(source)
        self.assertEqual(result['withdraw_candidate_ids'],['candidate:unhandled'])
        self.assertIn(candidate['action_id'],result['preserve_action_ids'])
        self.assertTrue(result['complete_action_ids'])

    def test_legacy_input_id_stays_opaque_in_snapshot_and_user_audit(self):
        source=self.sample();source['snapshot']['input_id']='synthetic/course/作业 1'+'字'*161
        check(source)
        controlled=apply_user_control(source,self.operation(source,'reopen_actions',['task:1']))
        self.assertEqual(controlled['audit']['input_id'],source['snapshot']['input_id'])
        self.assertEqual(self.evaluate(source)['disposition'],'submitted')


if __name__=='__main__':unittest.main()
