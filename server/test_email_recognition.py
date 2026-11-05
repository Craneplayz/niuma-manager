"""Email model settings and content-free timing/token transport regressions."""
from dataclasses import replace
import json
import os
from contextlib import closing
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import email_import
import recognition
import usage
from server.model_queue import ModelQueue
from ddl_text.codex_client import ENV_KEYS
from ddl_text.config import email_model_settings
from ddl_text.policy_version import text_policy_manifest
from ddl_text.tests.test_pipeline import example

class EmailThinkingTests(unittest.TestCase):
    options=dict(provider='qiniu',model='deepseek/deepseek-v4.1-flash',endpoint='https://api.qnaigc.com/v1',key='synthetic-only')

    def test_only_supported_email_provider_model_disables_thinking(self):
        self.assertEqual(email_model_settings(self.options).thinking_mode,'disabled')
        self.assertIsNone(email_model_settings(dict(self.options,provider='deepseek')).thinking_mode)
        self.assertIsNone(email_model_settings(dict(self.options,model='other-model')).thinking_mode)

    def test_policy_identity_matches_email_execution_settings(self):
        expected=text_policy_manifest(email_model_settings(self.options),provider='qiniu')
        with patch('ddl_text.codex_client.live_options',return_value=self.options):
            self.assertEqual(email_import.current_policy_version(),expected['version'])
        default=text_policy_manifest(replace(email_model_settings(self.options),thinking_mode=None),provider='qiniu')
        self.assertNotEqual(expected['version'],default['version'])
        no_fallback=text_policy_manifest(replace(email_model_settings(self.options),truncation_retry_tokens=None),provider='qiniu')
        self.assertNotEqual(expected['version'],no_fallback['version'])

    def test_runtime_keeps_email_budget_and_fallback_separate_from_manual_text(self):
        for platform,expected in [('email','disabled'),('manual','disabled')]:
            request=example('no-task')['input']
            request['records'][0]['platform']=platform
            with patch.dict(recognition.os.environ,{'TEXT_HARNESS_URL':''}),patch.object(recognition,'live_options',return_value=self.options),patch.object(recognition,'make_client'),patch.object(recognition,'process_request',return_value={'status':'no_tasks'}) as run:
                recognition.recognize(request,RuntimeError)
                self.assertEqual(run.call_args.args[2].thinking_mode,expected)
                self.assertEqual(run.call_args.args[2].timeout_seconds,270 if platform=='email' else 90)
                self.assertEqual(run.call_args.kwargs['budget_seconds'],270 if platform=='email' else 90)
                self.assertEqual(run.call_args.args[2].truncation_retry_tokens,16384 if platform=='email' else None)
                with patch.object(recognition.model_queue, 'disconnected', return_value=True):
                    self.assertEqual(run.call_args.kwargs['attempt_guard'](), platform!='email')

    def test_harness_http_wait_covers_email_budget_without_changing_manual_timeout(self):
        import io
        for platform,expected in [('email',275),('manual',95)]:
            request=example('no-task')['input'];request['records'][0]['platform']=platform
            with patch.dict(recognition.os.environ,{'TEXT_HARNESS_URL':'https://synthetic.invalid/recognize'}),patch.object(recognition,'urlopen',return_value=io.BytesIO(b'{"status":"no_tasks"}')) as opened:
                recognition.recognize(request,RuntimeError)
                self.assertEqual(opened.call_args.kwargs['timeout'],expected)


class SyntheticClient:
    def __init__(self, response='{"tasks": []}', fail=False): self.response, self.fail = response, fail
    def generate(self, *args):
        time.sleep(.025)
        if self.fail: raise RuntimeError('PRIVATE_PROVIDER_EXCEPTION_SENTINEL')
        return self.response


class SyntheticTokenClient(SyntheticClient):
    def generate(self, *args):
        self.response_metrics=dict(prompt_tokens=100,completion_tokens=8192,total_tokens=8292,
            reasoning_tokens=8192,finish_reason='length' if self.fail else 'stop',content='PRIVATE_TOKEN_SENTINEL')
        if self.fail:
            from ddl_text.adapters import AgentError
            raise AgentError('invalid_agent_output',True,'output_truncated')
        return super().generate(*args)


class SyntheticFallbackClient:
    def generate(self,instructions,record_json,settings,attempt):
        from ddl_text.adapters import AgentError
        first=settings.max_output_tokens==8192
        self.response_metrics=dict(prompt_tokens=100,completion_tokens=8192 if first else 5,
            total_tokens=8292 if first else 105,finish_reason='length' if first else 'stop')
        if first: raise AgentError('invalid_agent_output',True,'output_truncated')
        return '{"tasks":[]}'


class SyntheticCorrectiveClient:
    def __init__(self): self.calls = 0
    def generate(self, *args):
        self.calls += 1
        self.response_metrics = dict(total_tokens=10*self.calls,finish_reason='stop')
        return 'invalid synthetic JSON' if self.calls == 1 else '{"tasks":[]}'


class EmailRecognitionTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / 'metrics.sqlite3'
        with closing(sqlite3.connect(self.path)) as db, db: usage.initialize(db)
        self.env = patch.dict(os.environ, {**dict.fromkeys(ENV_KEYS, ''), 'TEXT_HARNESS_URL':''})
        self.env.start(); self.addCleanup(self.env.stop); self.addCleanup(self.temp.cleanup)
    def recognize(self, client, queue, email=True):
        options = dict(key='Synthetic-unused-key', provider='qiniu', model='synthetic', endpoint='https://synthetic.invalid')
        with patch.object(recognition, '_ACTIVE', queue), patch.object(recognition, 'live_options', return_value=options), patch.object(recognition, 'make_client', return_value=client), patch.object(recognition, 'text_policy_manifest', return_value={'components':recognition._POLICY_COMPONENTS,'version':'synthetic'}), recognition.telemetry_context(self.path, 'synthetic-owner'):
            request=example('no-task')['input']
            if email: request['records'][0]['platform']='email'
            return recognition.recognize(request, RuntimeError)
    def rows(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute('SELECT event,success,duration_ms FROM usage_events').fetchall()
    def test_queue_wait_is_separate_from_provider_and_validation(self):
        queue = ModelQueue(limit=1); self.assertTrue(queue.acquire())
        release = threading.Timer(.08, queue.release); release.start()
        try: result = self.recognize(SyntheticClient(), queue)
        finally: release.join()
        self.assertEqual(result['status'], 'no_tasks')
        rows = {row[0]:row for row in self.rows()}
        self.assertEqual(set(rows), {'email_model_queue','email_generation','email_validation'})
        self.assertGreaterEqual(rows['email_model_queue'][2], 40)
        self.assertGreaterEqual(rows['email_generation'][2], 15)
        self.assertLess(rows['email_validation'][2], rows['email_generation'][2])
        self.assertTrue(all(row[1] for row in rows.values()))
    def test_validation_failure_is_classified_without_raw_response(self):
        result = self.recognize(SyntheticClient('PRIVATE_RESPONSE_SENTINEL'), ModelQueue())
        self.assertEqual(result['status'], 'failed')
        with closing(sqlite3.connect(self.path)) as db, db:
            diagnostic = db.execute('SELECT stage,reason FROM email_recognition_diagnostics').fetchall()
            stored = repr(db.execute('SELECT * FROM email_recognition_diagnostics').fetchall()) + repr(self.rows())
        self.assertEqual(diagnostic, [('validation','invalid_json')])
        self.assertNotIn('PRIVATE_RESPONSE_SENTINEL',stored)
    def test_provider_failure_keeps_generation_duration_without_fake_validation(self):
        result = self.recognize(SyntheticClient(fail=True), ModelQueue())
        self.assertEqual(result['status'], 'failed')
        rows = {row[0]:row for row in self.rows()}
        self.assertEqual(rows['email_generation'][1], 0)
        self.assertGreaterEqual(rows['email_generation'][2], 15)
        self.assertNotIn('email_validation', rows)
        self.assertNotIn('PRIVATE_PROVIDER_EXCEPTION_SENTINEL',repr(result)+repr(rows))

    def test_token_usage_reaches_database_once_per_successful_or_failed_attempt(self):
        for fail in (False,True):
            self.recognize(SyntheticTokenClient(fail=fail),ModelQueue(),email=False)
        with closing(sqlite3.connect(self.path)) as db, db:
            rows=db.execute('SELECT success,finish_reason,prompt_tokens,completion_tokens,total_tokens,reasoning_tokens FROM email_model_token_usage ORDER BY created_at').fetchall()
        self.assertEqual(rows,[(1,'stop',100,8192,8292,8192),(0,'length',100,8192,8292,8192)])
        self.assertNotIn('PRIVATE_TOKEN_SENTINEL',repr(rows))

    def test_corrective_text_call_settles_both_provider_receipts(self):
        from server import model_queue
        budget = model_queue.ProviderBudget()
        receipt, _ = budget.reserve('synthetic',2,100000)
        with patch.object(model_queue,'PROVIDER',budget), \
             patch.object(model_queue,'provider_reservation',return_value=('synthetic',receipt,0)):
            result = self.recognize(SyntheticCorrectiveClient(),ModelQueue(),email=False)
        self.assertEqual(result['status'],'no_tasks')
        self.assertEqual(receipt[1][1:],[2,30])

    def test_email_fallback_persists_usage_for_both_attempts(self):
        result=self.recognize(SyntheticFallbackClient(),ModelQueue(),email=True)
        self.assertEqual(result['status'],'no_tasks')
        with closing(sqlite3.connect(self.path)) as db, db:
            rows=db.execute('SELECT success,finish_reason,completion_tokens FROM email_model_token_usage ORDER BY created_at').fetchall()
        self.assertEqual(rows,[(0,'length',8192),(1,'stop',5)])
        self.assertEqual(sum(row[2] for row in rows),8197)


if __name__ == "__main__":
    unittest.main()
