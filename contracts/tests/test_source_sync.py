import json
import unittest
from copy import deepcopy
from pathlib import Path
from jsonschema import Draft202012Validator
from contracts.source_sync import check, root_schema, SCHEMAS

EXAMPLES=json.loads((Path(__file__).parents[1]/'examples/source-sync-v01.json').read_text(encoding='utf-8'))


class SourceSyncContractTests(unittest.TestCase):
    def test_optional_collection_cause_is_validated_without_changing_legacy_runs(self):
        from contracts.tests.test_collection_failure import EXAMPLES as FAILURES
        value = deepcopy(EXAMPLES['run'])
        value['error'] = dict(code='collection_failed', message='采集未完成', retryable=True,
                              collection_failure=FAILURES[3])
        check('run', value)
        value['error']['collection_failure'] = dict(FAILURES[3], body='PRIVATE')
        with self.assertRaises(ValueError):
            check('run', value)
        value['error'].pop('collection_failure')
        check('run', value)

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

    def test_selective_retry_is_optional_bounded_and_unique(self):
        retry=deepcopy(EXAMPLES['retry'])
        check('retry',retry)
        retry.pop('request_ids')
        check('retry',retry)
        check('retry',dict(retry,request_ids=['snapshot:one']))
        for ids in ([],['same','same'],['x']*1001,[''],[123]):
            with self.assertRaises(ValueError):check('retry',dict(retry,request_ids=ids))

    def test_quiet_checks_use_last_status_without_a_history_row_and_legacy_fields_remain_optional(self):
        response=deepcopy(EXAMPLES['runs']);response['runs']=[]
        check('runs',response)
        response['last_check']['state']='recognizing'
        with self.assertRaises(ValueError):check('runs',response)
        legacy=deepcopy(EXAMPLES['run'])
        legacy.pop('updated_tasks');legacy['counts'].pop('review');legacy['counts'].pop('updated')
        for record in legacy['records']:record.pop('review',None)
        check('run',legacy)

    def test_review_and_update_counts_must_match_their_receipts(self):
        for name in ('review','updated'):
            value=deepcopy(EXAMPLES['run']);value['counts'][name]+=1
            with self.assertRaises(ValueError):check('run',value)

    def test_optional_named_results_must_match_counts_and_have_unique_candidate_identities(self):
        value=deepcopy(EXAMPLES['run'])
        record=value['records'][0]
        record['added_items']=[dict(candidate_id='created',task_id='task',label='实际任务',course=None)]
        record['added']=1;value['counts']['added']=sum(item['added'] for item in value['records'])
        check('run',value)
        record['added_items']=[]
        with self.assertRaises(ValueError):check('run',value)
        record['added']=2;value['counts']['added']+=1
        record['added_items']=[dict(candidate_id='same',task_id=None,label='重复',course=None)]*2
        with self.assertRaises(ValueError):check('run',value)

    def test_recovery_link_requires_a_recovered_receipt_and_explicit_run_and_request(self):
        value=deepcopy(EXAMPLES['run']);record=value['records'][1]
        record.update(recovered=True,recovered_by=dict(run_id='successful:run',request_id='successful:request'))
        check('run',value)
        record['recovered']=False
        with self.assertRaises(ValueError):check('run',value)
        record['recovered']=True;record['recovered_by']['run_id']=''
        with self.assertRaises(ValueError):check('run',value)

    def test_whole_collection_recovery_requires_an_empty_failed_collection_receipt_and_another_run(self):
        value=deepcopy(EXAMPLES['run'])
        value.update(records=[],updated_tasks=[],state='succeeded',skipped=0,
                     error=dict(code='collection_failed',message='采集失败',retryable=True),
                     recovered_by=dict(run_id='later:collection'))
        value['counts']={key:0 for key in value['counts']}
        check('run',value)
        value['state']='failed';check('run',value)  # Single-run receipts retain their original outcome.
        for edit in ('self','model','active','source'):
            invalid=deepcopy(value)
            if edit=='self':invalid['recovered_by']['run_id']=invalid['run_id']
            if edit=='model':invalid['error']['code']='recognition_failed'
            if edit=='active':invalid['state']='crawling'
            if edit=='source':invalid['records']=[deepcopy(EXAMPLES['run']['records'][0])]
            with self.assertRaises(ValueError):check('run',invalid)


if __name__=='__main__':unittest.main()
