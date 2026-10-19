"""Synthetic coverage for a content-free, read-only email response replay."""
import io
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ddl_text.adapters import AgentError
from ddl_text.chat_client import ChatClient, response_summary
from ddl_text.config import Settings
from ddl_text.tests.test_extraction import message
from server.diagnose_email_response import replay, test_options, load_record


class ResponseDiagnosticTests(unittest.TestCase):
    settings = Settings(mode='real', model='synthetic', endpoint='https://example.test/v1')

    def run_response(self, response, observer):
        raw = json.dumps(response).encode()
        with patch('ddl_text.chat_client.build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(raw)
            return replay(message(), ChatClient('synthetic-key', observer), self.settings)

    def test_truncated_reasoning_only_answer_is_identified_before_validation(self):
        responses=[]
        report=self.run_response({'choices':[{'finish_reason':'length','message':{'content':'','reasoning_content':'PRIVATE_SENTINEL'}}],
                                  'usage':{'completion_tokens':8192,'completion_tokens_details':{'reasoning_tokens':8192}}}, responses.append)
        self.assertEqual(report['diagnostic'],'output_truncated')
        self.assertFalse(report['validation_reached'])
        self.assertEqual(responses[0]['finish_reason'],'length')
        self.assertEqual(responses[0]['reasoning_tokens'],8192)
        self.assertEqual(responses[0]['content_chars'],0)
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps([report,responses]))

    def test_empty_answer_is_provider_failure(self):
        report=self.run_response({'choices':[{'finish_reason':'stop','message':{'content':''}}]},lambda _:None)
        self.assertEqual(report['diagnostic'],'provider_response')
        self.assertFalse(report['validation_reached'])

    def test_bad_task_json_reaches_validation(self):
        report=self.run_response({'choices':[{'finish_reason':'stop','message':{'content':'PRIVATE_SENTINEL'}}]},lambda _:None)
        self.assertTrue(report['validation_reached'])
        self.assertEqual(report['reason'],'invalid_json')
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(report))

    def test_valid_no_tasks_and_broken_observer_do_not_change_behavior(self):
        def broken(_): raise RuntimeError('PRIVATE_SENTINEL')
        report=self.run_response({'choices':[{'finish_reason':'stop','message':{'content':'{"tasks":[]}'}}]},broken)
        self.assertEqual(report['status'],'no_tasks')
        self.assertTrue(report['validation_reached'])

    def test_metadata_never_echoes_untrusted_strings(self):
        raw=json.dumps({'choices':[{'finish_reason':'PRIVATE_SENTINEL','message':{'content':'PRIVATE_SENTINEL','refusal':'PRIVATE_SENTINEL'}}],
                        'usage':{'prompt_tokens':'PRIVATE_SENTINEL','total_tokens':True}}).encode()
        summary=response_summary(raw)
        self.assertEqual(summary['finish_reason'],'other')
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(summary))
        self.assertNotIn('total_tokens',summary)
        self.assertFalse(response_summary(b'PRIVATE_SENTINEL')['json_valid'])

    def test_options_ignore_shell_credentials_and_endpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'.env.model'
            path.write_text('DDL_PROVIDER=qiniu\nQINIU_API_KEY=synthetic-test\n')
            with patch.dict(os.environ,{'QINIU_API_KEY':'foreign-key','DDL_ENDPOINT':'https://foreign.example/v1'}):
                options=test_options(path)
                self.assertEqual(options['key'],'synthetic-test')
                self.assertEqual(options['endpoint'],'https://api.qnaigc.com/v1')
                self.assertEqual(os.environ['QINIU_API_KEY'],'foreign-key')

    def test_saved_source_loader_is_read_only_and_requires_single_email(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.sqlite3'
            record=message(platform='email')
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE recognition_batches(request_id TEXT,request TEXT)')
                db.execute('INSERT INTO recognition_batches VALUES(?,?)',('saved',json.dumps({'contract_version':'0.1','request_id':'saved','records':[record]})))
                db.commit()
            before=path.read_bytes()
            self.assertEqual(load_record(path,'saved')['platform'],'email')
            self.assertEqual(path.read_bytes(),before)
            with self.assertRaises(ValueError):load_record(path,'missing')

    def test_reference_failure_reports_exact_check_without_private_quotes(self):
        from ddl_text.tests.test_extraction import draft
        from ddl_text.extraction import compile_drafts
        from server.diagnose_email_response import validation_detail
        r=message('PRIVATE_SENTINEL 原文。')
        for quote,source_id,expected in [('PRIVATE_SENTINEL 改写。',r['input_id'],'quote_not_verbatim'),('PRIVATE_SENTINEL', 'unknown-source','unknown_source_id')]:
            d=draft(r,evidence=[dict(input_id=source_id,quote=quote)])
            with self.assertRaises(AgentError) as caught:compile_drafts(json.dumps({'tasks':[d]}),r)
            detail=validation_detail(caught.exception)
            self.assertEqual(detail['check'],expected)
            self.assertEqual(detail['field'],'evidence')
            self.assertNotIn('PRIVATE_SENTINEL',json.dumps(detail))
            self.assertNotIn('unknown-source',json.dumps(detail))

    def test_whitespace_quote_mismatch_is_detected_without_relaxing_validation(self):
        from ddl_text.tests.test_extraction import draft
        from ddl_text.extraction import compile_drafts
        from server.diagnose_email_response import validation_detail
        r=message('PRIVATE_SENTINEL\n原文。')
        d=draft(r,evidence=[dict(input_id=r['input_id'],quote='PRIVATE_SENTINEL 原文。')])
        with self.assertRaises(AgentError) as caught:compile_drafts(json.dumps({'tasks':[d]}),r)
        detail=validation_detail(caught.exception)
        self.assertEqual(detail['check'],'quote_not_verbatim')
        self.assertTrue(detail['matches_after_whitespace_normalization'])
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(detail))

    def test_diagnostic_override_does_not_change_normal_email_settings(self):
        from server.diagnose_email_response import diagnostic_settings
        from ddl_text.config import email_model_settings
        options=dict(provider='qiniu',model='deepseek/deepseek-v4.1-flash',endpoint='https://api.qnaigc.com/v1')
        self.assertEqual(diagnostic_settings(options,240).timeout_seconds,240)
        self.assertEqual(diagnostic_settings(options,240).thinking_mode,'disabled')
        self.assertEqual(email_model_settings(options).timeout_seconds,270)
        self.assertEqual(diagnostic_settings(options).timeout_seconds,270)
        for value in (0,301,float('nan')):
            with self.assertRaises(ValueError):diagnostic_settings(options,value)
