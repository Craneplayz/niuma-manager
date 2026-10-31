"""Only confirmed token truncation gets one larger attempt, within one budget."""
import unittest
from dataclasses import replace
from unittest.mock import patch
from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.pipeline import process_request
from ddl_text.tests.test_pipeline import example


def truncated():
    error=AgentError('invalid_agent_output',True,'output_truncated')
    error.telemetry={'finish_reason':'length'}
    return error


class TruncationRetryTests(unittest.TestCase):
    settings=Settings(timeout_seconds=270,thinking_mode='disabled',truncation_retry_tokens=16384)

    def test_fallback_preserves_input_and_uses_remaining_total_budget(self):
        now=[0];calls=[]
        def attempt(adapter,record,settings,index,**kwargs):
            calls.append((settings.max_output_tokens,settings.timeout_seconds,index,record.copy()))
            if index==0:now[0]=70;raise truncated()
            return []
        with patch('ddl_text.pipeline.time.monotonic',side_effect=lambda:now[0]),patch('ddl_text.pipeline.run_attempt',side_effect=attempt):
            result=process_request(example('no-task')['input'],settings=self.settings,budget_seconds=270)
        self.assertEqual(result['status'],'no_tasks')
        self.assertEqual([(c[0],c[1],c[2]) for c in calls],[(8192,270,0),(16384,200,1)])
        self.assertEqual(calls[0][3],calls[1][3])

    def test_a_second_truncation_stops_after_two_calls(self):
        with patch('ddl_text.pipeline.run_attempt',side_effect=lambda *a,**kw:(_ for _ in ()).throw(truncated())) as run:
            result=process_request(example('no-task')['input'],settings=self.settings,budget_seconds=270)
        self.assertEqual(result['status'],'failed');self.assertEqual(run.call_count,2)

    def test_timeout_evidence_and_unconfirmed_size_errors_do_not_escalate(self):
        for error in (AgentError('agent_timeout',True),AgentError('invalid_agent_output',True,'draft_evidence'),AgentError('invalid_agent_output',True,'output_truncated')):
            with patch('ddl_text.pipeline.run_attempt',side_effect=error) as run:
                result=process_request(example('no-task')['input'],settings=self.settings,budget_seconds=270)
            self.assertEqual(result['status'],'failed');self.assertEqual(run.call_count,1)

    def test_no_fallback_after_cancellation_or_budget_expiry(self):
        now=[0]
        def attempt(*args,**kwargs):now[0]=270;raise truncated()
        with patch('ddl_text.pipeline.time.monotonic',side_effect=lambda:now[0]),patch('ddl_text.pipeline.run_attempt',side_effect=attempt) as run:
            result=process_request(example('no-task')['input'],settings=self.settings,budget_seconds=270)
        self.assertEqual(result['errors'][0]['code'],'agent_timeout');self.assertEqual(run.call_count,1)
        with patch('ddl_text.pipeline.run_attempt',side_effect=lambda *a,**kw:(_ for _ in ()).throw(truncated())) as run:
            result=process_request(example('no-task')['input'],settings=self.settings,budget_seconds=270,attempt_guard=iter([True,False]).__next__)
        self.assertEqual(result['status'],'failed');self.assertEqual(run.call_count,1)

    def test_fallback_is_opt_in_and_configuration_is_bounded(self):
        with patch('ddl_text.pipeline.run_attempt',side_effect=truncated()) as run:
            process_request(example('no-task')['input'],settings=Settings())
        self.assertEqual(run.call_count,1)
        for value in (0,True,8192,200000):
            with self.assertRaises(ValueError):replace(self.settings,truncation_retry_tokens=value)
