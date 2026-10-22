"""Synthetic provider-boundary regressions; no live model or private records."""
import copy
import json
import unittest
from unittest.mock import patch

from contracts.recognition_errors import CATALOG, describe, validate_failure
from contracts.build_recognition_errors import generate
from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.draft_recovery import recover
from ddl_text.extraction import ExtractionAdapter, compile_drafts
from ddl_text.media_pipeline import process_media_request
from ddl_text.media_worker import bounded
from ddl_text.pipeline import process_request
from ddl_text.tests.test_extraction import message, draft
from ddl_text.tests.media_cases import request_for, memory_loader, good_draft


class IndexedClient:
    def __init__(self, broken='required'):
        self.broken = broken

    def generate(self, instructions, record_json, settings, attempt):
        data = json.loads(record_json)
        assert 'text' not in data and 'evidence_catalog' in data
        quote = data['evidence_catalog']['q0']['quote']
        assert quote not in instructions
        task = dict(title='上ics课', description='明天上ics', notice_type='create', target=None,
                    course=dict(span_id='q0', value='ics'), prerequisites=[], evidence=[dict(span_id='q0')],
                    times=[], review_reasons=[], relevance=dict(state='current', reason='本次安排', evidence=[]))
        if 'validation_feedback' not in data or self.broken == 'persistent':
            if self.broken == 'json': return 'PRIVATE-SENTINEL invalid json'
            if self.broken == 'evidence': task['evidence'][0]['span_id'] = 'nonexistent'
            elif self.broken == 'course': task['course']['value'] = 'ICS'
            else: task.pop('prerequisites')
        return json.dumps(dict(tasks=[task]), ensure_ascii=False)


class CorrectingMediaClient:
    def __init__(self, persistent=False): self.persistent = persistent
    def complete_json(self, instructions, parts, settings):
        # Spawn starts fresh on each call, so use actual feedback, not a test counter.
        feedback = json.loads(parts[-1]['text']) if parts[-1].get('type') == 'text' else {}
        if 'validation_feedback' not in feedback or self.persistent:
            return '{"tasks": [], "image_quality": "PRIVATE-SENTINEL"}'
        return json.dumps(good_draft(), ensure_ascii=False)


def schema_failure():
    error = AgentError('invalid_agent_output', True, 'draft_schema')
    error.detail = dict(schema_path='properties/tasks/items/required', rule='required')
    raise error


class RecognitionErrorTests(unittest.TestCase):
    def test_missing_text_validator_is_dependency_failure_not_provider_failure(self):
        import builtins
        original=builtins.__import__
        def without_validator(name,*args,**kwargs):
            if name=='jsonschema': raise ModuleNotFoundError('synthetic missing validator')
            return original(name,*args,**kwargs)
        with patch('builtins.__import__',side_effect=without_validator):
            with self.assertRaises(AgentError) as caught:
                compile_drafts('{"tasks":[]}',message())
        self.assertEqual(caught.exception.diagnostic,'recognition_dependency')
        self.assertFalse(caught.exception.retryable)

    def test_historical_course_quotes_keep_upstream_and_validate_evidence(self):
        for upstream, quoted in (('ICS','ICS'),('数据科学','数据科学'),('主课程','讨论课')):
            record=message(quoted+'请交报告。',course_or_project=upstream)
            item=draft(record,course=dict(input_id=record['input_id'],quote=quoted,value=quoted))
            tasks=compile_drafts(json.dumps(dict(tasks=[item])),record)
            self.assertEqual(len(tasks),1)
            self.assertEqual(tasks[0]['course_or_project'],upstream)
            if upstream!=quoted:
                self.assertTrue(tasks[0]['needs_review'])
                self.assertTrue(any('课程' in reason for reason in tasks[0]['review_reasons']))
            item['course']['quote']='并不存在的证据'
            with self.assertRaises(AgentError): compile_drafts(json.dumps(dict(tasks=[item])),record)

    def test_catalog_contract_and_messages_are_content_free(self):
        generate(check=True)
        for reason in CATALOG:
            error = describe(dict(code='invalid_agent_output', message='PRIVATE-SENTINEL', retryable=True), reason=reason)
            validate_failure(error['failure'])
            self.assertEqual(error['code'], 'invalid_agent_output')
            self.assertNotIn('PRIVATE-SENTINEL', json.dumps(error))
            self.assertTrue(CATALOG[reason]['action'])
        invalid = describe(dict(code='invalid_agent_output', failure={'reason': []},
                           diagnostic={'schema_path':'PRIVATE-SENTINEL', 'rule':'required'}), reason=[])
        self.assertNotIn('diagnostic', invalid)
        self.assertEqual(invalid['failure']['reason'], 'result_contract')

    def test_text_repairs_schema_json_reference_and_literal_course(self):
        record = message('明天上ics')
        for broken in ('required', 'json', 'evidence', 'course'):
            with self.subTest(broken=broken):
                tasks = ExtractionAdapter(IndexedClient(broken)).extract(record, Settings(mode='real'), 0)
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]['evidence'][0]['quote'], '明天上ics')
                self.assertEqual(tasks[0]['course_or_project'], 'ics')
                self.assertIsNone(tasks[0]['deadline']['value'])

    def test_text_worker_preserves_failure_and_safe_validation_path(self):
        request = dict(contract_version='0.1', request_id='synthetic', records=[dict(input_id='m', text='明天上ics')])
        events = []
        result = process_request(request, ExtractionAdapter(IndexedClient('persistent')), Settings(mode='real', timeout_seconds=8),
                                 diagnostic_event=lambda stage, **fields: events.append(dict(stage=stage, **fields)))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['tasks'], [])
        error = result['errors'][0]
        self.assertEqual(error['failure']['reason'], 'draft_schema')
        self.assertEqual(error['diagnostic']['rule'], 'required')
        self.assertTrue(any(e.get('diagnostic') == 'draft_schema' for e in events))

    def test_media_recovers_inside_real_worker_and_keeps_visual_review(self):
        request, data = request_for()
        result = process_media_request(request, memory_loader(data), CorrectingMediaClient(), Settings(timeout_seconds=10))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['errors'], [])
        self.assertTrue(result['tasks'][0]['needs_review'])
        failed = process_media_request(request, memory_loader(data), CorrectingMediaClient(True), Settings(timeout_seconds=10))
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['errors'][0]['failure']['reason'], 'draft_schema')
        self.assertEqual(failed['errors'][0]['diagnostic']['rule'], 'type')
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(failed))

    def test_media_worker_keeps_sanitized_diagnostic(self):
        with self.assertRaises(AgentError) as caught: bounded(schema_failure, (), 5)
        self.assertEqual(caught.exception.diagnostic, 'draft_schema')
        self.assertEqual(caught.exception.detail['rule'], 'required')

    def test_repair_uses_remaining_budget_and_never_retries_transport(self):
        calls = []
        def call(settings, feedback):
            calls.append((settings.timeout_seconds, feedback))
            return 'first' if feedback is None else 'second'
        def compile_result(raw):
            if raw == 'first': raise AgentError('invalid_agent_output', True, 'draft_json')
            return ['ok']
        with patch('ddl_text.draft_recovery.time.monotonic', side_effect=[10, 16]):
            self.assertEqual(recover(call, compile_result, Settings(timeout_seconds=10)), ['ok'])
        self.assertEqual([c[0] for c in calls], [10, 4])
        for reason in ('model_timeout', 'model_rate_limited', 'provider_response', 'output_truncated'):
            calls.clear()
            def reject(_): raise AgentError('agent_unavailable', True, reason)
            with self.assertRaises(AgentError): recover(call, reject, Settings(timeout_seconds=10))
            self.assertEqual(len(calls), 1)

    def test_no_third_response_or_oversize_feedback_or_expired_budget(self):
        for raw, times, expected in [('bad', [10,11], 2), ('x'*64001, [10,11], 1), ('bad', [10,20], 1)]:
            calls = []
            def call(settings, feedback): calls.append(feedback); return raw
            def reject(_): raise AgentError('invalid_agent_output', True, 'draft_json')
            with patch('ddl_text.draft_recovery.time.monotonic', side_effect=times), self.assertRaises(AgentError):
                recover(call, reject, Settings(timeout_seconds=10))
            self.assertEqual(len(calls), expected)

    def test_legacy_compilation_separates_schema_and_fabricated_evidence(self):
        record = message()
        task = draft(record)
        for mutation, reason in [('schema','draft_schema'), ('evidence','draft_evidence')]:
            value = copy.deepcopy(task)
            if mutation == 'schema': value.pop('prerequisites')
            else: value['evidence'][0]['quote'] = 'PRIVATE-SENTINEL'
            with self.assertRaises(AgentError) as caught: compile_drafts(json.dumps(dict(tasks=[value])), record)
            self.assertEqual(caught.exception.diagnostic, reason)


if __name__ == '__main__': unittest.main()
