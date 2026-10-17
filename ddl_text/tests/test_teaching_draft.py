"""Policy/semantic boundary regression; no model access or personal snapshots."""
import json
import time
import unittest
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from contracts.teaching_review_draft import validate_pair
from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.teaching_draft import (compile_teaching_draft, process_teaching_draft,
                                    teaching_draft_schema, validate_teaching_record, evidence_catalog,
                                    compile_teaching_response, TeachingDraftAdapter)
from ddl_text.tests.teaching_cases import POLICY_CASES, model_task, policy_case, raw_task


@dataclass
class SyntheticClient:
    raw: str
    delay: float = 0

    def complete_json(self, instructions, payload, settings):
        time.sleep(self.delay)
        return self.raw


class TeachingDraftTests(unittest.TestCase):
    def test_assignment_heading_date_is_not_due_but_same_day_deadline_is(self):
        def dated(record, task):
            record.update(text='第4周周三 9月30日 作业\n教材习题17.4第2、4、5、6、8题。', references=[],body_status='complete')
            task['assessment']['action']['evidence']=[dict(part_id='body',quote=record['text'])]
            task['assessment']['deadline'].update(value='2026-09-30',precision='date',derivation='absolute',anchor=None,
                evidence=[dict(part_id='body',quote=record['text'])])
            task['assessment']['unread_relations']=[]
            task['relevance']['evidence']=[]
        decision=self.compile('explicit-complete',dated)[0]
        self.assertEqual(decision['recommendation'],'task')
        self.assertEqual(decision['pair']['assessment']['deadline']['derivation'],'not_provided')
        def due(record,task):
            dated(record,task);record['text']+='请在9月30日23:59前提交。'
            task['assessment']['deadline']['evidence']=[dict(part_id='body',quote=record['text'])]
        self.assertEqual(self.compile('explicit-complete',due)[0]['pair']['assessment']['deadline']['value'],'2026-09-30')
        def assigned(record,task):
            dated(record,task);task['time_role']='assigned'
        self.assertIsNone(self.compile('explicit-complete',assigned)[0]['pair']['assessment']['deadline']['value'])
        def unusual_deadline(record,task):
            dated(record,task);record['text']+='老师当晚收取本次作业。';task['time_role']='deadline'
        self.assertEqual(self.compile('explicit-complete',unusual_deadline)[0]['pair']['assessment']['deadline']['value'],'2026-09-30')

    def test_schema_error_retains_only_rule_and_schema_path(self):
        def invalid(record,task):task['assessment']['deadline']['value']='private invalid date'
        with self.assertRaises(AgentError) as failure:self.compile('explicit-complete',invalid)
        detail=failure.exception.detail
        self.assertIn('deadline',detail['schema_path'])
        self.assertNotIn('private',json.dumps(detail))

    def test_notice_has_evidence_without_inventing_action_or_deadline(self):
        def change(record, task):
            record.update(text='本学期上课地点为理教408。', references=[], body_status='complete')
            task.update(content_type='notice', title='上课地点：理教408', description=record['text'])
            task['assessment'].update(action=dict(state='supported', evidence=[dict(part_id='body', quote=record['text'])]),
                deadline=dict(value=None, precision='unknown', derivation='not_provided', anchor=None, evidence=[]),
                unread_relations=[])
        decision=self.compile('explicit-complete',change)[0]
        self.assertEqual((decision['content_type'],decision['recommendation']),('notice','task'))
        self.assertIsNone(decision['pair']['assessment']['deadline']['value'])

    def test_notice_preserves_deadline_and_rejects_unknown_content_type(self):
        decision=self.compile('explicit-complete',lambda r,t:t.update(content_type='notice'))[0]
        self.assertEqual(decision['content_type'],'notice')
        self.assertEqual(decision['pair']['assessment']['deadline']['value'],'2026-10-23')
        for kind in ('event',''):
            with self.subTest(kind=kind),self.assertRaises(AgentError):
                self.compile('explicit-complete',lambda r,t:t.update(content_type=kind))

    def test_old_model_draft_without_content_type_remains_task(self):
        decision=self.compile('explicit-complete',lambda r,t:t.pop('content_type'))[0]
        self.assertEqual(decision['content_type'],'task')

    def test_indexed_evidence_resolves_exact_source_without_recopying_pdf(self):
        pair=policy_case('explicit-complete')['pair'];record=pair['record']
        record['text']='请交作业。\nPDF提取有\t空格\n与换行。\n2026年10月23日前提交。'
        catalog=evidence_catalog(record);task=model_task(pair)
        body_id=next(k for k,q in catalog.items() if q['part_id']=='body')
        task['assessment']['action']['evidence']=[dict(span_id=body_id)]
        task['assessment']['deadline']['evidence']=[dict(span_id=body_id)]
        decisions=compile_teaching_draft(json.dumps(dict(tasks=[task])),record,source_verified=True,indexed=True)
        self.assertEqual(decisions[0]['pair']['assessment']['action']['evidence'],[catalog[body_id]])
        for bad in ('invented', 'another-record:q0'):
            task['assessment']['action']['evidence']=[dict(span_id=bad)]
            with self.assertRaises(AgentError):
                compile_teaching_draft(json.dumps(dict(tasks=[task])),record,source_verified=True,indexed=True)

    def test_catalog_covers_all_read_text_and_no_unread_text(self):
        record=policy_case('unread-supplement')['pair']['record']
        record['text']='行一\n'+'PDF字符\t与空格。'*100
        catalog=evidence_catalog(record)
        self.assertEqual(''.join(q['quote'] for q in catalog.values() if q['part_id']=='body'),record['text'])
        self.assertTrue(all(len(q['quote'])<=240 for q in catalog.values()))
        self.assertFalse(any(q['part_id'].startswith('ref:') for q in catalog.values()))

    def test_safe_diagnostics_survive_worker_and_distinguish_validation_stages(self):
        record=policy_case()['pair']['record']
        cases=[('not JSON','draft_json'),('{"tasks":[{}]}','draft_schema')]
        task=model_task(policy_case()['pair']);task['assessment']['action']['evidence']=[dict(part_id='body',quote='private invented quote')]
        cases.append((json.dumps(dict(tasks=[task])),'draft_evidence'))
        for raw,reason in cases:
            with self.subTest(reason=reason),self.assertRaises(AgentError) as error:
                process_teaching_draft(record,client=SyntheticClient(raw),settings=Settings(timeout_seconds=10))
            self.assertEqual(error.exception.diagnostic,reason)
            self.assertNotIn('private',str(error.exception))
            if reason=='draft_schema':
                self.assertEqual(error.exception.detail['rule'],'required')
                self.assertIn('tasks',error.exception.detail['schema_path'])
            else:self.assertIsNone(error.exception.detail)
        self.assertIsNone(AgentError(diagnostic='private arbitrary exception').diagnostic)
        from ddl_text.adapters import safe_schema_detail
        self.assertIsNone(safe_schema_detail(dict(schema_path='properties/private-course-name',rule='required')))

    def compile(self, case_name='unread-supplement', change=None, verified=None):
        case = policy_case(case_name)
        record, task = case['pair']['record'], model_task(case['pair'])
        if change: change(record, task)
        return compile_teaching_draft(json.dumps(dict(tasks=[task]), ensure_ascii=False), record,
            source_verified=case['source_verified'] if verified is None else verified)

    def test_model_task_survives_partial_unknown_attachment_and_year_completion(self):
        for name in ('explicit-complete','unread-supplement','yearless-unknown-age','cross-year-term'):
            def change(record,task):
                record['body_status']='partial'
                for relation in task['assessment']['unread_relations']:relation.update(role='unknown',evidence=[])
                task['assessment']['other_review_reasons']=['An unrelated informational note']
            with self.subTest(name=name):
                decision=self.compile(name,change)[0]
                self.assertEqual(decision['recommendation'],'task')
                self.assertEqual(decision['review_codes'],[])
                self.assertEqual(decision['review_reasons'],[])

    def test_model_candidate_and_filter_are_not_overridden_by_complete_metadata(self):
        for route,reason in [('candidate','The final deadline is in an unread correction'),('filtered','This is a past assignment')]:
            def change(record,task):task.update(recommendation=route,reason=reason)
            decision=self.compile('explicit-complete',change)[0]
            self.assertEqual(decision['recommendation'],route)
            self.assertEqual(decision['filter_reason'],reason if route=='filtered' else None)
            self.assertEqual(decision['review_reasons'],[reason] if route=='candidate' else [])

    def test_ongoing_and_expired_dates_are_classified_by_model_not_python(self):
        def change(record,task):record['collected_at']='2026-11-01T12:00:00+08:00'
        decision=self.compile('explicit-complete',change)[0]
        self.assertEqual(decision['recommendation'],'task')
        # A real model must distinguish overdue/ongoing in the skill; this test
        # proves code does not secretly perform a second semantic classification.

    def test_per_task_classification_does_not_spread_one_attachment_doubt(self):
        pair=policy_case()['pair'];record=pair['record']
        first=model_task(pair);second=model_task(pair,'candidate','Final date depends on the unread correction')
        decisions=compile_teaching_draft(json.dumps(dict(tasks=[first,second])),record,source_verified=True)
        self.assertEqual([d['recommendation'] for d in decisions],['task','candidate'])
        self.assertNotEqual(decisions[0]['pair']['assessment']['candidate_id'],decisions[1]['pair']['assessment']['candidate_id'])

    def test_missing_or_inconsistent_classifications_fail_instead_of_entering_review(self):
        for error in ('missing_route','missing_reason','unknown_route','unresolved_task','filter_without_evidence'):
            def change(record,task):
                if error=='missing_route':task.pop('recommendation')
                elif error=='missing_reason':task.update(recommendation='candidate',reason=' ')
                elif error=='unknown_route':task['recommendation']='maybe'
                elif error=='unresolved_task':task['assessment']['deadline'].update(value=None,precision='unknown',derivation='unresolved',anchor=None)
                else:
                    task.update(recommendation='filtered',reason='No action')
                    task['assessment']['action'].update(state='uncertain',evidence=[])
            with self.subTest(error=error),self.assertRaises(AgentError):self.compile('explicit-complete',change)

    def test_change_marked_ready_keeps_new_deadline_and_waits_for_linking(self):
        for operation in ('update', 'postpone', 'cancel', 'uncertain'):
            def change(record, task):
                task['notice_type'] = operation
                task['target'] = dict(part_id='body', quote=record['text'])
            with self.subTest(operation=operation):
                decision = self.compile('explicit-complete', change)[0]
                self.assertEqual(decision['recommendation'], 'candidate')
                self.assertEqual(decision['notice_type'], operation)
                self.assertEqual(decision['pair']['assessment']['deadline'],
                    policy_case('explicit-complete')['pair']['assessment']['deadline'])
                self.assertEqual(decision['review_reasons'], ['变更通知需关联现有条目后处理。'])
                self.assertTrue(decision['target']['quote'])
        def forged_change(record, task):
            task.update(notice_type='update', target=dict(part_id='body', quote='invented target'))
        with self.assertRaises(AgentError): self.compile('explicit-complete', forged_change)

    def test_courseware_empty_decision_does_not_require_reading_unrelated_attachment(self):
        from ddl_text.tests.teaching_cases import empty_reply
        record = policy_case()['pair']['record']
        record['text'] = '第四章 字符串\n第四章课件.pdf'
        record['body_status'] = 'partial'
        raw = json.loads(empty_reply(record, 'no_tasks'))
        raw['empty_decision']['reason'] = '仅发布普通课件，无必做行动或影响安排的信息。'
        result = compile_teaching_response(json.dumps(raw), record, source_verified=True)
        self.assertEqual(result['decisions'], [])
        self.assertEqual(result['empty_decision']['status'], 'no_tasks')

    def test_change_route_compatibility_does_not_spend_another_model_call(self):
        pair = policy_case('explicit-complete')['pair']
        draft = model_task(pair)
        draft.update(notice_type='update', target=None)
        draft['assessment']['action']['evidence'] = [dict(span_id='q0')]
        draft['assessment']['deadline']['evidence'] = [dict(span_id='q0')]
        class Client:
            calls = 0
            def complete_json(self, instructions, payload, settings):
                self.calls += 1
                return json.dumps(dict(tasks=[draft]))
        client = Client()
        result = TeachingDraftAdapter(client, True).extract(pair['record'], Settings(mode='real'), 0)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result[0]['decisions'][0]['recommendation'], 'candidate')
        self.assertEqual(result[0]['decisions'][0]['pair']['assessment']['deadline']['value'],
            draft['assessment']['deadline']['value'])

    def test_new_empty_decisions_distinguish_non_tasks_from_missing_source(self):
        from ddl_text.tests.teaching_cases import empty_reply
        from ddl_text.teaching_draft import compile_teaching_response
        record=policy_case()['pair']['record'];record['body_status']='partial'
        for status in ('no_tasks','incomplete'):
            out=compile_teaching_response(empty_reply(record,status),record,source_verified=True)
            self.assertEqual(out['decisions'],[])
            self.assertEqual(out['empty_decision']['status'],status)
        with self.assertRaises(AgentError):compile_teaching_response('{"tasks":[]}',record,source_verified=True)
        raw=json.loads(empty_reply(record));raw['empty_decision']['evidence'][0]['quote']='invented'
        with self.assertRaises(AgentError):compile_teaching_response(json.dumps(raw),record,source_verified=True)

    def test_captured_metadata_is_available_in_all_evidence_slots(self):
        def add_metadata(record,task):
            task['assessment']['deadline']['evidence'].append(dict(part_id='source_metadata',quote=record['source_metadata_text']))
        self.assertEqual(self.compile('yearless-unknown-age',add_metadata)[0]['recommendation'],'task')
        def only_metadata(record,task):
            task['assessment']['deadline']['evidence']=[dict(part_id='source_metadata',quote=record['source_metadata_text'])]
        self.assertEqual(self.compile('yearless-unknown-age',only_metadata)[0]['recommendation'],'task')
        def action_metadata(record,task):
            task['assessment']['action']['evidence']=[dict(part_id='source_metadata',quote=record['source_metadata_text'])]
        self.assertEqual(self.compile('explicit-complete',action_metadata)[0]['recommendation'],'task')

    def test_empty_classification_accepts_body_and_metadata_catalog_spans(self):
        record=policy_case()['pair']['record']
        catalog=evidence_catalog(record)
        for status in ('no_tasks','incomplete'):
            raw=dict(tasks=[],empty_decision=dict(status=status,reason='材料不足或无待执行要求。',
                evidence=[dict(span_id=k) for k in catalog]))
            out=compile_teaching_response(json.dumps(raw),record,source_verified=True,indexed=True)
            self.assertEqual(out['empty_decision']['status'],status)
            self.assertEqual(out['empty_decision']['evidence'],list(catalog.values()))
        raw['empty_decision']['evidence']=[dict(span_id='not-captured')]
        with self.assertRaises(AgentError):compile_teaching_response(json.dumps(raw),record,source_verified=True,indexed=True)

    def test_unread_relation_annotations_are_optional_but_cannot_invent_sources(self):
        self.assertEqual(self.compile(change=lambda r,t:t['assessment'].update(unread_relations=[]))[0]['recommendation'],'task')
        def no_quote(record,task):
            task['relevance'].update(state='ongoing',evidence=[])
            for rel in task['assessment']['unread_relations']:rel.update(role='supplementary',evidence=[])
        self.assertEqual(self.compile(change=no_quote)[0]['recommendation'],'task')

    def test_invented_reference_and_forged_quote_rejected(self):
        for field in ('unknown_relation', 'fake_quote', 'metadata_quote'):
            def change(record, task):
                if field == 'unknown_relation': task['assessment']['unread_relations'][0]['ref_id']='not-in-record'
                else: task['assessment']['action']['evidence'] = [dict(
                    part_id='source_metadata' if field == 'metadata_quote' else 'body',
                    quote='invented action')]
            with self.subTest(field=field), self.assertRaises(AgentError) as error:
                self.compile(change=change)
            self.assertEqual(error.exception.code, 'invalid_agent_output')

    def test_source_trust_cannot_come_from_json_or_model(self):
        case = policy_case('yearless-current-assignment-term')
        record = case['pair']['record']
        result = compile_teaching_draft(raw_task(case['pair']), record)[0]
        self.assertEqual(result['recommendation'], 'candidate')
        self.assertIn('source_unverified', result['review_codes'])
        record['source_verified'] = True
        with self.assertRaises(AgentError) as error: validate_teaching_record(record)
        self.assertEqual(error.exception.code, 'invalid_input')
        with self.assertRaises(AgentError): self.compile(change=lambda r, t: t.update(source_verified=True))

    def test_historical_deadline_filtered_without_rebasing_to_current_year(self):
        def change(record,task):task.update(recommendation='filtered',reason='已失效的历史作业。')
        decision = self.compile('old-publication-keeps-old-year',change)[0]
        self.assertEqual(decision['recommendation'], 'filtered')
        self.assertTrue(decision['pair']['assessment']['deadline']['value'].startswith('2023-'))
        self.assertTrue(decision['filter_reason'])

    def test_unverified_model_filter_is_not_allowed_to_hide_source(self):
        def change(record,task):task.update(recommendation='filtered',reason='Old assignment')
        d=self.compile('old-publication-keeps-old-year',change,verified=False)[0]
        self.assertEqual(d['recommendation'],'candidate')
        self.assertIn('source_unverified',d['review_codes'])

    def test_prompt_schema_limits_quotes_to_actual_read_parts(self):
        record = policy_case()['pair']['record']
        schema = teaching_draft_schema(record)
        self.assertEqual(schema['$defs']['quote']['properties']['part_id']['enum'], ['body','source_metadata'])
        relevance = schema['properties']['tasks']['items']['properties']['relevance']
        self.assertEqual(relevance['properties']['evidence']['items']['properties']['part_id']['enum'],
                         ['body', 'source_metadata'])

    def test_invalid_model_json_and_dates_are_errors_not_no_tasks(self):
        record = policy_case()['pair']['record']
        for raw in ('{"tasks":[],"tasks":[]}', '{"tasks":[],"source_verified":true}', 'not json'):
            with self.subTest(raw=raw), self.assertRaises(AgentError) as error:
                compile_teaching_draft(raw, record)
            self.assertEqual(error.exception.code, 'invalid_agent_output')
        with self.assertRaises(AgentError):
            self.compile(change=lambda r, t: t['assessment']['deadline'].update(value='2026-02-30'))

    def test_invalid_source_is_rejected_before_model(self):
        record = policy_case()['pair']['record']
        record['source_verified'] = True
        with self.assertRaises(AgentError) as error:
            process_teaching_draft(record, client=None, settings=Settings())
        self.assertEqual(error.exception.code, 'invalid_input')

    def test_worker_preserves_results_and_source(self):
        case = policy_case()
        before = deepcopy(case['pair']['record'])
        preview = process_teaching_draft(before, client=SyntheticClient(raw_task(case['pair'])),
                                        settings=Settings(timeout_seconds=10), source_verified=True)
        self.assertEqual(preview.decisions[0]['recommendation'], 'task')
        self.assertEqual(before, case['pair']['record'])
        self.assertFalse(preview.no_action_complete)

    def test_worker_transports_empty_classification_and_preserves_source_trust(self):
        from ddl_text.tests.teaching_cases import empty_reply
        for name,verified,complete in [('unread-supplement',True,True),('explicit-complete',False,False)]:
            record=policy_case(name)['pair']['record']
            preview=process_teaching_draft(record,client=SyntheticClient(empty_reply(record)),
                settings=Settings(timeout_seconds=10),source_verified=verified)
            self.assertEqual(preview.no_action_complete,complete)
            self.assertEqual(preview.empty_decision['status'],'no_tasks' if complete else 'incomplete')
            self.assertTrue(preview.warnings)

    def test_worker_timeout_is_not_an_empty_result(self):
        with self.assertRaises(AgentError) as error:
            process_teaching_draft(policy_case()['pair']['record'],
                client=SyntheticClient('{"tasks":[]}', delay=5), settings=Settings(timeout_seconds=0.1))
        self.assertEqual(error.exception.code, 'agent_timeout')

    def test_real_adapter_corrects_once_with_original_budget_and_explicit_feedback(self):
        record=policy_case('explicit-complete')['pair']['record']
        valid=json.dumps(dict(tasks=[],empty_decision=dict(status='incomplete',reason='缺少材料。',evidence=[dict(span_id='q0')])))
        class Client:
            def __init__(self,responses):self.responses=iter(responses);self.calls=[]
            def complete_json(self,instructions,payload,settings):
                self.calls.append((instructions,json.loads(payload),settings.timeout_seconds))
                return next(self.responses)
        client=Client(['{"tasks":[{}]}',valid])
        with patch('ddl_text.teaching_draft.time.monotonic',side_effect=[10,12]):
            out=TeachingDraftAdapter(client,True).extract(record,Settings(mode='real',timeout_seconds=10),0)
        self.assertEqual(out[0]['empty_decision']['status'],'incomplete')
        self.assertEqual([c[2] for c in client.calls],[10,8])
        self.assertIn('validation_feedback',client.calls[1][1])
        self.assertIn('required shape=',client.calls[1][1]['validation_feedback'])
        self.assertIn('submission_relation',client.calls[1][1]['validation_feedback'])
        self.assertEqual(client.calls[1][1]['previous_response'],'{"tasks":[{}]}')
        self.assertEqual(client.calls[0][1]['record_metadata'],client.calls[1][1]['record_metadata'])
        for responses,budget,expected_calls in [(['bad','bad'],10,2),(['bad'],1,1)]:
            client=Client(responses)
            with patch('ddl_text.teaching_draft.time.monotonic',side_effect=[10,12]),self.assertRaises(AgentError):
                TeachingDraftAdapter(client,True).extract(record,Settings(mode='real',timeout_seconds=budget),0)
            self.assertEqual(len(client.calls),expected_calls)

    def test_output_fields_are_derived_from_shared_assessment(self):
        from contracts.teaching_review_draft import SCHEMA
        actual = teaching_draft_schema()['properties']['tasks']['items']['properties']['assessment']
        expected = deepcopy(SCHEMA['properties']['assessment'])
        for key in ('candidate_id', 'input_id'):
            expected['properties'].pop(key); expected['required'].remove(key)
        self.assertEqual(actual, expected)



if __name__ == '__main__':
    unittest.main()
