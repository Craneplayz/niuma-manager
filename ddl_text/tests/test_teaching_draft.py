"""Policy/semantic boundary regression; no model access or personal snapshots."""
import json
import time
import unittest
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from contracts.teaching_review_draft import validate_pair
from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.teaching_draft import (compile_teaching_draft, process_teaching_draft,
                                    teaching_draft_schema, validate_teaching_record, evidence_catalog)
from ddl_text.tests.teaching_cases import POLICY_CASES, model_task, policy_case, raw_task


@dataclass
class SyntheticClient:
    raw: str
    delay: float = 0

    def complete_json(self, instructions, payload, settings):
        time.sleep(self.delay)
        return self.raw


class TeachingDraftTests(unittest.TestCase):
    def test_indexed_evidence_resolves_exact_source_without_recopying_pdf(self):
        pair=policy_case('explicit-complete')['pair'];record=pair['record']
        record['text']='请交作业。\nPDF提取有\t空格\n与换行。\n2026年10月23日前提交。'
        catalog=evidence_catalog(record);task=model_task(pair)
        body_id=next(k for k,q in catalog.items() if q['part_id']=='body')
        task['assessment']['action']['evidence']=[dict(span_id=body_id)]
        task['assessment']['deadline']['evidence']=[dict(span_id=body_id)]
        decisions=compile_teaching_draft(json.dumps(dict(tasks=[task])),record,source_verified=True,indexed=True)
        self.assertEqual(decisions[0]['pair']['assessment']['action']['evidence'],[catalog[body_id]])
        for bad in ('invented', next(k for k,q in catalog.items() if q['part_id']=='source_metadata')):
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
        self.assertIsNone(AgentError(diagnostic='private arbitrary exception').diagnostic)

    def compile(self, case_name='unread-supplement', change=None, verified=None):
        case = policy_case(case_name)
        record, task = case['pair']['record'], model_task(case['pair'])
        if change: change(record, task)
        return compile_teaching_draft(json.dumps(dict(tasks=[task]), ensure_ascii=False), record,
            source_verified=case['source_verified'] if verified is None else verified)

    def test_shared_twenty_cases_survive_compilation(self):
        for case in POLICY_CASES:
            with self.subTest(name=case['name']):
                result = self.compile(case['name'])[0]
                self.assertEqual(result['review_codes'], case['expected_review_codes'])
                validate_pair(result['pair'])

    def test_supplementary_attachment_does_not_force_review_or_claim_read(self):
        decision = self.compile()[0]
        self.assertEqual(decision['recommendation'], 'task')
        self.assertEqual(decision['review_reasons'], [])
        self.assertEqual(decision['pair']['record']['references'][0]['read_state'], 'unread')

    def test_term_year_completion_is_quiet_but_unknown_age_remains_reviewable(self):
        decision = self.compile('yearless-current-assignment-term')[0]
        self.assertEqual(decision['recommendation'], 'task')
        self.assertEqual(decision['review_reasons'], [])
        self.assertEqual(decision['pair']['record']['published_at'], None)
        unknown = self.compile('yearless-unknown-age')[0]
        self.assertEqual(unknown['recommendation'], 'candidate')
        self.assertIn('original_age_unknown', unknown['review_codes'])

    def test_cross_year_and_outside_term(self):
        self.assertEqual(self.compile('cross-year-term')[0]['recommendation'], 'task')
        outside = self.compile('outside-term-do-not-discard')[0]
        self.assertEqual(outside['recommendation'], 'candidate')
        self.assertIn('term_date_unresolved', outside['review_codes'])

    def test_no_year_date_is_not_checked_against_new_raw_format_list(self):
        def change(record, task):
            record['text'] = '作业在十月廿三号以前交齐。'
            for field in ('action', 'deadline'):
                task['assessment'][field]['evidence'] = [dict(part_id='body', quote=record['text'])]
        self.assertEqual(self.compile('yearless-current-assignment-term', change)[0]['recommendation'], 'task')

    def test_multitask_attachment_relation_is_per_task(self):
        case = policy_case()
        record = case['pair']['record']
        record['text'] += '第三次作业的提交日期见附加练习。'
        first = model_task(case['pair'])
        second = deepcopy(first)
        second['title'] = '完成第三次作业'
        quote = [dict(part_id='body', quote='第三次作业的提交日期见附加练习。')]
        second['assessment'].update(action=dict(state='supported', evidence=quote),
            deadline=dict(value=None, precision='unknown', derivation='unresolved', anchor=None, evidence=quote),
            unread_relations=[dict(ref_id='extra', role='required', evidence=quote)])
        decisions = compile_teaching_draft(json.dumps(dict(tasks=[first, second]), ensure_ascii=False), record,
                                          source_verified=True)
        self.assertEqual([d['recommendation'] for d in decisions], ['task', 'candidate'])
        self.assertNotEqual(decisions[0]['pair']['assessment']['candidate_id'], decisions[1]['pair']['assessment']['candidate_id'])

    def test_omitted_unread_reference_and_forged_quote_rejected(self):
        for field in ('missing_relation', 'fake_quote', 'metadata_quote'):
            def change(record, task):
                if field == 'missing_relation': task['assessment']['unread_relations'] = []
                else: task['assessment']['action']['evidence'] = [dict(
                    part_id='source_metadata' if field == 'metadata_quote' else 'body',
                    quote=record['source_metadata_text'] if field == 'metadata_quote' else 'invented action')]
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
        decision = self.compile('old-publication-keeps-old-year')[0]
        self.assertEqual(decision['recommendation'], 'filtered')
        self.assertTrue(decision['pair']['assessment']['deadline']['value'].startswith('2023-'))
        self.assertTrue(decision['filter_reason'])
        self.assertEqual(self.compile('old-publication-cannot-refresh-year')[0]['recommendation'], 'candidate')

    def test_ongoing_future_and_changes_survive_historical_filter(self):
        def ongoing(record, task):
            record['text'] += '逾期仍须补交。'
            task['relevance'] = dict(state='ongoing', reason='仍需完成', evidence=[dict(part_id='body', quote='逾期仍须补交。')])
        self.assertEqual(self.compile('old-publication-keeps-old-year', ongoing)[0]['recommendation'], 'candidate')
        def future(record, task):
            record['text'] = '请于2027年1月3日交报告。'
            quotes = [dict(part_id='body', quote=record['text'])]
            task['assessment']['action']['evidence'] = quotes
            task['assessment']['deadline'].update(value='2027-01-03', derivation='absolute', anchor=None, evidence=quotes)
        self.assertEqual(self.compile('old-publication-keeps-old-year', future)[0]['recommendation'], 'task')
        def cancel(record, task):
            task['notice_type'] = 'cancel'
            task['target'] = dict(part_id='body', quote=record['text'])
        self.assertEqual(self.compile('old-publication-keeps-old-year', cancel)[0]['recommendation'], 'candidate')

    def test_unverified_old_source_is_not_silently_discarded(self):
        self.assertEqual(self.compile('old-publication-keeps-old-year', verified=False)[0]['recommendation'], 'candidate')

    def test_metadata_can_support_history_but_not_action(self):
        def history(record, task):
            record['source_metadata_text'] = '这是2023年布置的旧作业。'
            task['relevance'] = dict(state='historical', reason='旧作业',
                evidence=[dict(part_id='source_metadata', quote=record['source_metadata_text'])])
        self.assertEqual(self.compile('old-publication-keeps-old-year', history)[0]['recommendation'], 'filtered')
        self.assertEqual(self.compile('old-publication-keeps-old-year', history, verified=False)[0]['recommendation'], 'candidate')

    def test_prompt_schema_limits_quotes_to_actual_read_parts(self):
        record = policy_case()['pair']['record']
        schema = teaching_draft_schema(record)
        self.assertEqual(schema['$defs']['quote']['properties']['part_id']['enum'], ['body'])
        relevance = schema['properties']['tasks']['items']['properties']['relevance']
        self.assertEqual(relevance['properties']['evidence']['items']['properties']['part_id']['enum'],
                         ['body', 'source_metadata'])

    def test_explicit_old_year_stays_old_even_in_current_course(self):
        def change(record, task):
            record['text'] = '请于2023年10月23日交第二次作业。'
            quote = [dict(part_id='body', quote=record['text'])]
            task['assessment']['action']['evidence'] = quote
            task['assessment']['deadline'].update(value='2023-10-23', derivation='absolute', anchor=None, evidence=quote)
        self.assertEqual(self.compile('yearless-current-assignment-term', change)[0]['recommendation'], 'filtered')

    def test_recently_overdue_is_filtered_without_thirty_day_grace(self):
        def change(record, task): record['collected_at'] = '2026-10-24T12:00:00+08:00'
        decision = self.compile('explicit-complete', change)[0]
        self.assertEqual(decision['recommendation'], 'filtered')
        self.assertEqual(decision['review_reasons'], [])
        self.assertIn('在采集时已过', decision['filter_reason'])

    def test_date_only_deadline_survives_entire_source_local_day(self):
        for collected, expected in [('2026-10-23T15:59:59Z', 'task'),
                                    ('2026-10-23T16:00:00Z', 'filtered')]:
            def change(record, task): record['collected_at'] = collected
            with self.subTest(collected=collected):
                self.assertEqual(self.compile('explicit-complete', change)[0]['recommendation'], expected)

    def test_datetime_expires_only_after_its_instant(self):
        for collected, expected in [('2026-10-23T09:59:59Z', 'task'),
                                    ('2026-10-23T10:00:00Z', 'task'),
                                    ('2026-10-23T10:00:01Z', 'filtered')]:
            def change(record, task):
                record.update(text='请于2026年10月23日18点交作业。', collected_at=collected)
                evidence = [dict(part_id='body', quote=record['text'])]
                task['assessment']['action']['evidence'] = evidence
                task['assessment']['deadline'].update(value='2026-10-23T18:00:00+08:00',
                    precision='datetime', evidence=evidence)
            with self.subTest(collected=collected):
                self.assertEqual(self.compile('explicit-complete', change)[0]['recommendation'], expected)

    def test_recent_overdue_does_not_override_real_uncertainty(self):
        for case in ('yearless-unknown-age', 'unread-authority'):
            def change(record, task): record['collected_at'] = '2026-10-24T12:00:00+08:00'
            with self.subTest(case=case):
                self.assertEqual(self.compile(case, change)[0]['recommendation'], 'candidate')
        def incomplete(record, task):
            record.update(collected_at='2026-10-24T12:00:00+08:00', body_status='partial')
        self.assertEqual(self.compile('explicit-complete', incomplete)[0]['recommendation'], 'candidate')

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

    def test_empty_partial_and_unverified_inputs_cannot_claim_no_tasks(self):
        for name, verified, complete in [('unread-supplement', True, False),
                                         ('explicit-complete', True, True),
                                         ('explicit-complete', False, False)]:
            with self.subTest(name=name, verified=verified):
                preview = process_teaching_draft(policy_case(name)['pair']['record'],
                    client=SyntheticClient('{"tasks":[]}'), settings=Settings(timeout_seconds=10),
                    source_verified=verified)
                self.assertEqual(preview.no_action_complete, complete)
                self.assertEqual(bool(preview.warnings), not complete)

    def test_worker_timeout_is_not_an_empty_result(self):
        with self.assertRaises(AgentError) as error:
            process_teaching_draft(policy_case()['pair']['record'],
                client=SyntheticClient('{"tasks":[]}', delay=5), settings=Settings(timeout_seconds=0.1))
        self.assertEqual(error.exception.code, 'agent_timeout')

    def test_output_fields_are_derived_from_shared_assessment(self):
        from contracts.teaching_review_draft import SCHEMA
        actual = teaching_draft_schema()['properties']['tasks']['items']['properties']['assessment']
        expected = deepcopy(SCHEMA['properties']['assessment'])
        for key in ('candidate_id', 'input_id'):
            expected['properties'].pop(key); expected['required'].remove(key)
        self.assertEqual(actual, expected)

    def test_saved_live_drafts_match_current_compiler(self):
        path = Path(__file__).resolve().parents[1] / 'docs/archive/2026-10-07-teaching-policy-live.json'
        self.assertTrue(path.exists(), 'synthetic live archive is part of this delivery')
        from ddl_text.tests.teaching_cases import live_cases, matches
        expected = {c['name']: c for c in live_cases()}
        for row in json.loads(path.read_text(encoding='utf-8'))['cases']:
            with self.subTest(name=row['name']):
                self.assertEqual(row['record'], expected[row['name']]['record'])
                self.assertEqual(row['expected'], expected[row['name']]['expected'])
                decisions = compile_teaching_draft(row['model_draft'], row['record'], source_verified=row['source_verified'])
                self.assertEqual(decisions, row['decisions'])
                self.assertTrue(matches(row, decisions))

    def test_live_check_does_not_allow_required_file_to_be_auto_accepted(self):
        from ddl_text.tests.teaching_cases import live_cases, matches
        case = next(c for c in live_cases() if c['name'] == 'unread-authority')
        decisions = self.compile('unread-authority')
        self.assertTrue(matches(case, decisions))
        changed = deepcopy(decisions)
        changed[0]['pair']['assessment']['deadline'].update(value=None, precision='unknown', derivation='unresolved', anchor=None)
        self.assertTrue(matches(case, changed))
        for change in ('route', 'missing_reason', 'false_no_deadline', 'wrong_date'):
            bad = deepcopy(decisions)
            if change == 'route': bad[0]['recommendation'] = 'task'
            elif change == 'missing_reason': bad[0]['review_codes'] = []
            elif change == 'false_no_deadline': bad[0]['pair']['assessment']['deadline'].update(value=None, derivation='not_provided')
            else: bad[0]['pair']['assessment']['deadline']['value'] = '2030-01-01'
            with self.subTest(change=change): self.assertFalse(matches(case, bad))


if __name__ == '__main__':
    unittest.main()
