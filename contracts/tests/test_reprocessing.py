from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from contracts.reprocessing import (ROOT, bundle, root_schema, check, check_start_run,
    same_operation, check_transition, association_plan, may_commit)

EXAMPLES=json.loads((ROOT/'examples/reprocessing-v01-cases.json').read_text(encoding='utf-8'))


class ReprocessingTests(unittest.TestCase):
    def samples(self):return deepcopy(EXAMPLES)

    def test_standalone_roots_and_all_shared_examples(self):
        self.assertEqual(json.loads((ROOT/'schemas/reprocessing-v0.1.schema.json').read_text(encoding='utf-8')),bundle())
        for kind in bundle()['$defs']:Draft202012Validator.check_schema(root_schema(kind))
        for kind in ('start','sources','stop','error','runs'):check(kind,EXAMPLES[kind])
        for case in EXAMPLES['lifecycle']:
            for snapshot in case['snapshots']:check_start_run(EXAMPLES['start'],snapshot)
            for before,after in zip(case['snapshots'],case['snapshots'][1:]):check_transition(before,after)
        for case in EXAMPLES['association_cases']:
            self.assertEqual(association_plan(case['input']),case['expected'],case['name'])

    def test_start_rejects_empty_all_records_duplicates_and_client_provenance(self):
        for change in ('empty','duplicate','raw','trust','account','policy'):
            start=self.samples()['start']
            if change=='empty':start['input_ids']=[]
            elif change=='duplicate':start['input_ids']*=2
            else:start[{'raw':'text','trust':'source_verified','account':'user_id','policy':'policy_version'}[change]]='forged'
            with self.subTest(change=change),self.assertRaises(Exception):check('start',start)

    def test_idempotence_ignores_order_but_not_changed_selection_or_source(self):
        first=self.samples()['start'];retry=deepcopy(first);retry['input_ids'].reverse()
        self.assertTrue(same_operation(first,retry))
        retry['source_account_id']='source:other';self.assertFalse(same_operation(first,retry))
        retry=deepcopy(first);retry['input_ids']=['another'];self.assertFalse(same_operation(first,retry))

    def test_run_cannot_rewrite_selection_or_source(self):
        data=self.samples();run=data['lifecycle'][0]['snapshots'][0]
        run['source_account_id']='source:other'
        with self.assertRaises(ValueError):check_start_run(data['start'],run)
        run['source_account_id']=data['start']['source_account_id'];run['records'][0]['input_id']='another'
        with self.assertRaises(ValueError):check_start_run(data['start'],run)

    def test_failure_preserves_old_result_and_does_not_apply_candidates(self):
        for change in ('lost_old','outcome','summary','version_not_attempted'):
            run=self.samples()['lifecycle'][1]['snapshots'][-1];row=run['records'][1]
            if change=='lost_old':row['old_result_retained']=False
            elif change=='outcome':row['outcome']='tasks'
            elif change=='summary':row['decision_summary']['new_pending']=1
            else:row['attempted']=False
            with self.subTest(change=change),self.assertRaises(ValueError):check('run',run)

    def test_success_requires_actual_policy_not_demo_or_unknown_version(self):
        for version in (None,'teaching-policy-v1:'+'a'*64,'text-policy-v1:unknown'):
            run=self.samples()['lifecycle'][0]['snapshots'][-1];run['records'][0]['policy_version']=version
            with self.subTest(version=version),self.assertRaises(Exception):check('run',run)

    def test_counts_and_statuses_cannot_hide_unprocessed_work(self):
        for change in ('counts','failed','success','stopped','duplicate'):
            run=self.samples()['lifecycle'][0]['snapshots'][0]
            if change=='counts':run['counts']['succeeded']=1
            elif change=='duplicate':run['records'][1]=deepcopy(run['records'][0])
            else:run['state']={'failed':'failed','success':'succeeded','stopped':'stopped'}[change]
            with self.subTest(change=change),self.assertRaises(ValueError):check('run',run)

    def test_stop_and_later_run_prevent_late_commits(self):
        snaps=self.samples()['lifecycle'][2]['snapshots'];running=snaps[1];stopping=snaps[2]
        self.assertTrue(may_commit(running,'source:record:1',latest_run_id=running['run_id']))
        self.assertFalse(may_commit(running,'source:record:1',latest_run_id='run:newer'))
        self.assertFalse(may_commit(stopping,'source:record:1',latest_run_id=running['run_id']))
        late=self.samples()['lifecycle'][0]['snapshots'][-1];late['revision']=4
        with self.assertRaises(ValueError):check_transition(stopping,late)

    def test_terminal_identity_and_version_are_immutable(self):
        before=self.samples()['lifecycle'][0]['snapshots'][1]
        for change in ('revision','identity','policy'):
            after=self.samples()['lifecycle'][0]['snapshots'][-1]
            if change=='revision':after['revision']+=1
            elif change=='identity':after['operation_id']='op:another'
            else:after['records'][0]['policy_version']='text-policy-v1:'+'b'*64
            with self.subTest(change=change),self.assertRaises(ValueError):check_transition(before,after)
        end=self.samples()['lifecycle'][0]['snapshots'][-1]
        self.assertEqual(check_transition(end,deepcopy(end)),end)
        other=deepcopy(end);other['revision']+=1
        with self.assertRaises(ValueError):check_transition(end,other)

    def test_all_user_decisions_survive_unmatched_or_empty_new_outputs(self):
        for decision in ('confirmed','ignored','edited','completed','deleted'):
            source=self.samples()['association_cases'][1]['input'];source['previous'][0]['decision']=decision
            self.assertTrue(association_plan(source)['hold_record'])
            source['candidates']=[]
            plan=association_plan(source)
            self.assertEqual(plan['preserved_action_ids'],['action:ignored'])

    def test_associations_reject_many_to_one_unknown_and_conflicting_new(self):
        for change in ('many_to_one','unknown','conflicting_new'):
            value=self.samples()['association_cases'][0]['input']
            if change=='many_to_one':value['links'][1]['action_id']=value['links'][0]['action_id']
            elif change=='unknown':value['links'][0]['candidate_id']='unknown'
            else:value['confirmed_new']=[value['links'][0]['candidate_id']]
            with self.subTest(change=change),self.assertRaises(ValueError):association_plan(value)

    def test_sources_are_bounded_and_unique_per_account(self):
        sources=self.samples()['sources'];sources['accounts'][0]['records']*=2
        with self.assertRaises(ValueError):check('sources',sources)
        sources=self.samples()['sources'];sources['accounts'][0]['label']='private\nline'
        with self.assertRaises(Exception):check('sources',sources)


if __name__=='__main__':unittest.main()
