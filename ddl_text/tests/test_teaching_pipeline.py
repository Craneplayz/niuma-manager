"""Batch consumer, provenance and reprocessing handoff using synthetic records."""
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch

from contracts.teaching_io import check_pair, snapshot_digest
from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.chat_client import ChatClient
from ddl_text.teaching_pipeline import process_teaching_request, validate_teaching_result
from ddl_text.teaching_policy import teaching_policy_manifest
from ddl_text.tests.teaching_cases import model_task, policy_case, empty_reply


@dataclass
class RecordClient:
    drafts: dict

    def complete_json(self,instructions,payload,settings):
        record=json.loads(payload)['record']
        return self.drafts[record['input_id']]


def example(names=('explicit-complete',)):
    entries=[];drafts={}
    for index,name in enumerate(names):
        pair=policy_case(name)['pair'];record=pair['record']
        record['input_id']+=':'+str(index);pair['assessment']['input_id']=record['input_id']
        entries.append(dict(record=record,source=dict(label='合成教学网通知',url=None,course_or_project='测试课程')))
        drafts[record['input_id']]=json.dumps(dict(tasks=[model_task(pair)]),ensure_ascii=False)
    return dict(contract_version='0.4',request_id='synthetic:teaching-batch',records=entries),drafts


class TeachingPipelineTests(unittest.TestCase):
    def run_batch(self,request,drafts,verified=True,**kwargs):
        trusted={snapshot_digest(e) for e in request['records']} if verified else set()
        return process_teaching_request(request,client=RecordClient(drafts),settings=Settings(timeout_seconds=10),
                                        verified_snapshot_digests=trusted,**kwargs)

    def test_mixed_decisions_keep_each_course_source_and_policy_gate(self):
        request,drafts=example(('unread-supplement','yearless-current-assignment-term','yearless-unknown-age'))
        before=deepcopy(request);result=self.run_batch(request,drafts)
        self.assertEqual(result['status'],'success',result);self.assertIsNone(result['policy_version'])
        self.assertEqual([r['decisions'][0]['recommendation'] for r in result['record_results']],['task','task','task'])
        self.assertEqual(result['record_results'][1]['decisions'][0]['pair']['assessment']['deadline']['value'],'2026-10-23')
        self.assertEqual(result['record_results'][0]['source']['course_or_project'],'测试课程')
        self.assertEqual(request,before)

    def test_failed_record_does_not_hide_successful_or_later_records(self):
        request,drafts=example(('explicit-complete','explicit-complete','explicit-complete'))
        drafts[request['records'][1]['record']['input_id']]='not json'
        result=self.run_batch(request,drafts)
        self.assertEqual(result['status'],'partial_success')
        self.assertEqual([r['status'] for r in result['record_results']],['success','failed','success'])
        self.assertEqual(result['record_results'][1]['error']['code'],'invalid_agent_output')

    def test_empty_readable_unread_and_unverified_are_distinguished(self):
        for name,verified,status in [('explicit-complete',True,'no_tasks'),('unread-supplement',True,'no_tasks'),('explicit-complete',False,'incomplete')]:
            with self.subTest(name=name,verified=verified):
                request,drafts=example((name,));drafts={key:empty_reply(request['records'][0]['record']) for key in drafts}
                result=self.run_batch(request,drafts,verified)
                self.assertEqual(result['record_results'][0]['status'],status)
                self.assertEqual(result['status'],'no_tasks' if status=='no_tasks' else 'failed')

    def test_default_trust_does_not_promote_valid_proposals(self):
        request,drafts=example()
        result=process_teaching_request(request,client=RecordClient(drafts),settings=Settings(timeout_seconds=10))
        decision=result['record_results'][0]['decisions'][0]
        self.assertEqual(decision['recommendation'],'candidate')
        self.assertIn('source_unverified',decision['review_codes'])

    def test_changed_snapshot_and_duplicate_records_fail_before_execution(self):
        request,drafts=example();trusted={snapshot_digest(request['records'][0])}
        request['records'][0]['record']['text']+='Changed.'
        with patch('ddl_text.teaching_pipeline.process_teaching_draft') as call:
            with self.assertRaises(AgentError):process_teaching_request(request,client=None,settings=Settings(),verified_snapshot_digests=trusted)
            request['records']*=2
            with self.assertRaises(AgentError):self.run_batch(request,drafts)
            call.assert_not_called()

    def test_budget_expiration_records_failures_without_additional_calls(self):
        request,drafts=example(('explicit-complete','explicit-complete'))
        with patch('ddl_text.teaching_pipeline.time.monotonic',side_effect=[0,2,3]),patch('ddl_text.teaching_pipeline.process_teaching_draft') as call:
            result=self.run_batch(request,drafts,budget_seconds=1)
        self.assertEqual(result['status'],'failed');call.assert_not_called()
        self.assertTrue(all(r['error']['code']=='agent_timeout' for r in result['record_results']))
        self.assertTrue(all(not r['attempted'] for r in result['record_results']))

    def test_consumer_checks_classification_consistency_and_metadata(self):
        request,drafts=example();trusted={snapshot_digest(e) for e in request['records']}
        result=self.run_batch(request,drafts)
        forged=deepcopy(result);d=forged['record_results'][0]['decisions'][0]
        d.update(recommendation='filtered',filter_reason=None)
        with self.assertRaises(ValueError):validate_teaching_result(request,forged,verified_snapshot_digests=trusted)
        forged=deepcopy(result);forged['record_results'][0]['source']['course_or_project']='wrong course'
        with self.assertRaises(ValueError):check_pair(request,forged,verified_snapshot_digests=trusted)

    def test_result_size_limit_preserves_normal_later_results(self):
        request,drafts=example(('explicit-complete','explicit-complete'))
        first=request['records'][0]['record']['input_id']
        payload=json.loads(drafts[first]);payload['tasks']*=50
        for task in payload['tasks']:task['description']='说明'*1800
        drafts[first]=json.dumps(payload,ensure_ascii=False)
        with patch('ddl_text.teaching_pipeline.MAX_BYTES',25000):
            result=self.run_batch(request,drafts)
        self.assertEqual([r['status'] for r in result['record_results']],['failed','success'])
        self.assertEqual(result['record_results'][0]['error']['code'],'limit_exceeded')

    def test_shared_examples_are_consumable_without_model(self):
        path=Path(__file__).resolve().parents[2]/'contracts/examples/teaching-v04-cases.json'
        for case in json.loads(path.read_text(encoding='utf-8'))['cases']:
            with self.subTest(name=case['name']):
                trusted={snapshot_digest(e) for e in case['request']['records']} if case['verified'] else set()
                validate_teaching_result(case['request'],case['expected'],verified_snapshot_digests=trusted)

    def test_legacy_live_results_remain_readable_without_reclassifying(self):
        path=Path(__file__).resolve().parents[1]/'docs/archive/2026-10-07-teaching-policy-live.json'
        for case in json.loads(path.read_text(encoding='utf-8'))['cases']:
            request=dict(contract_version='0.4',request_id='archive:'+case['name'],records=[dict(
                record=case['record'],source=dict(label='合成通知',url=None,course_or_project=None))])
            result=dict(contract_version='0.4',request_id=request['request_id'],policy_version=None,status='success',
                record_results=[dict(input_id=case['record']['input_id'],snapshot_id=case['record']['snapshot_id'],
                    source=request['records'][0]['source'],attempted=True,status='success',decisions=case['decisions'],warnings=[],error=None)])
            trusted={snapshot_digest(e) for e in request['records']} if case['source_verified'] else set()
            self.assertEqual(validate_teaching_result(request,result,verified_snapshot_digests=trusted),result)


class TeachingPolicyTests(unittest.TestCase):
    def test_real_transport_uses_teaching_schema_and_records_actual_policy(self):
        request,drafts=example();calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path,payload))
                document=json.loads(payload['messages'][1]['content'])
                record=document['record_metadata']
                def indexed(value):
                    if isinstance(value,dict):
                        if set(value)=={'part_id','quote'}:
                            return dict(span_id=next(k for k,q in document['evidence_catalog'].items()
                                if q['part_id']==value['part_id'] and value['quote'] in q['quote']))
                        return {k:indexed(v) for k,v in value.items()}
                    if isinstance(value,list):return [indexed(v) for v in value]
                    return value
                reply=dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(indexed(json.loads(drafts[record['input_id']])))))])
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(reply).encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            settings=Settings(mode='real',model='synthetic-teaching',timeout_seconds=10,
                              endpoint='http://127.0.0.1:%d/v1'%server.server_port)
            result=process_teaching_request(request,client=ChatClient('synthetic-secret'),settings=settings,
                verified_snapshot_digests={snapshot_digest(e) for e in request['records']})
            self.assertEqual(result['status'],'success',result)
            self.assertTrue(result['record_results'][0]['attempted'])
            self.assertEqual(result['policy_version'],teaching_policy_manifest(settings)['version'])
            self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],'/v1/chat/completions')
            self.assertIn('unread_relations',calls[0][1]['messages'][0]['content'])
            self.assertEqual(calls[0][1]['model'],settings.model)
            self.assertNotIn('synthetic-secret',json.dumps(result))
            # A release edit during execution invalidates the result instead of
            # labelling it as an old policy's successful run.
            with patch('ddl_text.teaching_pipeline.teaching_policy_manifest',
                       side_effect=[{'version':result['policy_version']},{'version':result['policy_version']},ValueError('changed')]):
                changed=process_teaching_request(request,client=ChatClient('synthetic-secret'),settings=settings)
            self.assertEqual(changed['record_results'][0]['error']['code'],'agent_unavailable')
            self.assertFalse(changed['record_results'][0]['decisions'])
        finally:
            server.shutdown();server.server_close();thread.join(5)

    def test_actual_configuration_identity_is_separate_from_text_policy(self):
        from dataclasses import replace
        from ddl_text.policy_version import text_policy_manifest
        settings=Settings(mode='real',model='synthetic',endpoint='https://example.invalid/v1',timeout_seconds=45)
        first=teaching_policy_manifest(settings)
        self.assertTrue(first['version'].startswith('teaching-policy-v1:'))
        self.assertNotEqual(first['version'],text_policy_manifest(settings,provider='qiniu')['version'])
        self.assertEqual(first,teaching_policy_manifest(replace(settings,credential_env='ANOTHER_KEY')))
        self.assertNotEqual(first['version'],teaching_policy_manifest(replace(settings,model='changed'))['version'])
        self.assertNotEqual(first['version'],teaching_policy_manifest(replace(settings,thinking_mode='disabled'))['version'])
        self.assertIn('contracts/teaching_io.py',first['components'])
        with patch('ddl_text.teaching_policy.LOADED_COMPONENTS',{}):
            with self.assertRaises(ValueError):teaching_policy_manifest(settings)


if __name__=='__main__':unittest.main()
