"""Primary-source evidence is required for inclusion, filtering and no_tasks.

These are deterministic producer/consumer contract tests, not model-quality
measurements. Incomplete input and pre-context stored outputs stay compatible.
"""
import json
import unittest
from copy import deepcopy

from contracts.course_context import VERSION, revision
from contracts.teaching_io import aggregate_status, check_pair, snapshot_digest
from ddl_text.adapters import AgentError
from ddl_text.teaching_draft import compile_teaching_response
from ddl_text.tests.teaching_cases import model_task, policy_case


def sample():
    pair=policy_case('explicit-complete')['pair']
    record=pair['record']
    record.update(text='第四章作业已经取消，本条不再要求提交。',references=[],
        source_metadata_text='学期：2026-autumn',
        course_term=dict(term_id='2026-autumn',starts_on='2026-09-01',ends_on='2027-02-28',
            applies_to='current_assignment',evidence=[dict(part_id='source_metadata',quote='学期：2026-autumn')]))
    context_text='另一批次作业已提交；课程资料说明本次作业取消。'
    ref=dict(ref_id='context:other',kind='course_context',label='课程补充资料',read_state='complete',text=context_text)
    document=dict(ref_id=ref['ref_id'],input_id='other-input',snapshot_id='snapshot:other',
        course_id=record['course_id'],term_id=record['course_term']['term_id'],part_id='body',
        source_url=None,source_label=ref['label'],published_at=None,offset=0,length=len(context_text))
    record['references']=[ref]
    record['course_context']=dict(contract_version=VERSION,documents=[document],omitted_documents=0,
        revision=revision([dict(document,text=context_text)]))
    primary=dict(part_id='body',quote=record['text'])
    other=dict(part_id=ref['ref_id'],quote=context_text)
    task=model_task(pair,recommendation='filtered',reason='当前作业已取消。')
    task['assessment'].update(action=dict(state='supported',evidence=[primary]),
        deadline=dict(value=None,precision='unknown',derivation='not_provided',anchor=None,evidence=[]),
        unread_relations=[])
    task['relevance']['evidence']=[other]
    request=dict(contract_version='0.4',request_id='request:context-boundary',
        records=[dict(record=record,source=dict(label='当前作业',url=None,course_or_project='合成课程'))])
    return request,task,primary,other


def compile_result(request,envelope):
    return compile_teaching_response(json.dumps(envelope,ensure_ascii=False),
                                     request['records'][0]['record'],source_verified=True)


def result_envelope(request,compiled):
    entry=request['records'][0]
    empty=compiled['empty_decision']
    status='success' if compiled['decisions'] else empty['status']
    row=dict(input_id=entry['record']['input_id'],snapshot_id=entry['record']['snapshot_id'],
        source=deepcopy(entry['source']),attempted=True,status=status,decisions=compiled['decisions'],
        warnings=['source_incomplete'] if status=='incomplete' else [],error=None,empty_decision=empty)
    return dict(contract_version='0.4',classification_mode='model-v1',request_id=request['request_id'],
                policy_version=None,status=aggregate_status([row]),record_results=[row])


class CourseContextEvidenceBoundaryTests(unittest.TestCase):
    def check(self,request,result):
        return check_pair(request,result,verified_snapshot_digests={snapshot_digest(e) for e in request['records']})

    def test_filtered_requires_primary_action_or_relevance_evidence(self):
        for location in ('action','relevance'):
            request,task,primary,other=sample()
            task['assessment']['action']['evidence']=[other]
            task['relevance']['evidence']=[other]
            if location=='action':task['assessment']['action']['evidence'].append(primary)
            else:task['relevance']['evidence'].append(primary)
            with self.subTest(location=location):
                result=result_envelope(request,compile_result(request,dict(tasks=[task])))
                self.check(request,result)
                # A stored/independently produced result must obey the same gate.
                decision=result['record_results'][0]['decisions'][0]
                decision['pair']['assessment']['action']['evidence']=[other]
                decision['relevance']['evidence']=[other]
                with self.assertRaisesRegex(ValueError,'primary-source evidence'):self.check(request,result)
        request,task,_,other=sample()
        task['assessment']['action']['evidence']=[other]
        task['relevance']['evidence']=[other]
        with self.assertRaises(AgentError) as failure:compile_result(request,dict(tasks=[task]))
        self.assertEqual(failure.exception.code,'invalid_agent_output')

    def test_no_tasks_rejects_only_context_in_producer_and_consumer(self):
        request,_,primary,other=sample()
        empty=dict(status='no_tasks',reason='当前来源已明确取消作业。',evidence=[primary,other])
        result=result_envelope(request,compile_result(request,dict(tasks=[],empty_decision=empty)))
        self.check(request,result)
        result['record_results'][0]['empty_decision']['evidence']=[other]
        with self.assertRaisesRegex(ValueError,'primary-source evidence'):self.check(request,result)
        empty['evidence']=[other]
        with self.assertRaises(AgentError) as failure:compile_result(request,dict(tasks=[],empty_decision=empty))
        self.assertEqual(failure.exception.code,'invalid_agent_output')

    def test_current_attachment_is_primary_evidence(self):
        request,task,_,_=sample()
        ref=dict(ref_id='primary:file',kind='attachment',label='当前作业.pdf',read_state='complete',
                 text='本批次作业已取消，无需提交。')
        request['records'][0]['record']['references'].append(ref)
        evidence=dict(part_id=ref['ref_id'],quote=ref['text'])
        task['assessment']['action']['evidence']=[evidence]
        self.check(request,result_envelope(request,compile_result(request,dict(tasks=[task]))))
        empty=dict(status='no_tasks',reason='当前附件明确取消要求。',evidence=[evidence])
        self.check(request,result_envelope(request,compile_result(request,dict(tasks=[],empty_decision=empty))))

    def test_incomplete_does_not_require_invented_primary_evidence(self):
        request,_,_,other=sample()
        empty=dict(status='incomplete',reason='其他资料不能确定当前来源的要求。',evidence=[other])
        self.check(request,result_envelope(request,compile_result(request,dict(tasks=[],empty_decision=empty))))

    def test_without_course_context_keeps_existing_filter_and_empty_contract(self):
        request,task,primary,_=sample()
        record=request['records'][0]['record']
        record.pop('course_context');record['references']=[]
        task['relevance']['evidence']=[primary]
        self.check(request,result_envelope(request,compile_result(request,dict(tasks=[task]))))
        empty=dict(status='no_tasks',reason='当前来源没有尚待完成的要求。',evidence=[primary])
        self.check(request,result_envelope(request,compile_result(request,dict(tasks=[],empty_decision=empty))))


if __name__=='__main__':unittest.main()
