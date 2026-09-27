"""Synthetic HTTP checks; no public model calls or real credentials."""

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from ddl_text.adapters import AgentError
from ddl_text.chat_client import ChatClient
from ddl_text.codex_client import ROOT, live_options
from ddl_text.config import Settings
from ddl_text.extraction import ExtractionAdapter, load_instructions
from ddl_text.pipeline import process_request
from ddl_text.provider import make_client


class ProviderTests(unittest.TestCase):
    def test_provider_credentials_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / '.env'
            path.write_text('DDL_PROVIDER=qiniu\nDEEPSEEK_API_KEY=old-secret\nQINIU_API_KEY=\n', encoding='utf-8')
            options = live_options(path)
            self.assertEqual(options['key'], '')
            self.assertEqual(options['model'], 'deepseek/deepseek-v4.1-flash')
            self.assertEqual(options['endpoint'], 'https://api.qnaigc.com/v1')
            with patch('ddl_text.provider.preflight', side_effect=AssertionError('Codex must not run')):
                self.assertIsInstance(make_client(options), ChatClient)
            with patch.dict(os.environ, {'QINIU_API_KEY': 'synthetic'}):
                self.assertEqual(live_options(path)['key'], 'synthetic')
            for endpoint in ('https://api.deepseek.com', 'https://api.qnaigc.com/bypass/openai/v1',
                             'https://api.qnaigc.com/v1/chat/completions'):
                with patch.dict(os.environ, {'DDL_ENDPOINT': endpoint}), self.assertRaises(ValueError):
                    live_options(path)
            path.write_text('DEEPSEEK_API_KEY=old-secret\n', encoding='utf-8')
            self.assertEqual(live_options(path)['provider'], 'deepseek')
            self.assertEqual(live_options(path)['key'], 'old-secret')


class ChatClientTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.status = 200
        self.delay = 0
        self.fixture = json.loads((ROOT / 'docs/examples/text-stage-c-drafts.json').read_text(encoding='utf-8'))[0]
        self.reply = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.fixture['response'])}}]}
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                owner.calls.append((self.path, self.headers.get('Authorization'),
                                    json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                time.sleep(owner.delay)
                try:
                    self.send_response(owner.status)
                    if owner.status == 307:
                        self.send_header('Location', 'http://127.0.0.1:%d/redirected' % self.server.server_port)
                    self.end_headers()
                    self.wfile.write(json.dumps(owner.reply).encode())
                except OSError:
                    pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.settings = Settings(mode='real', model='deepseek/deepseek-v4.1-flash',
                                 endpoint='http://127.0.0.1:%d/v1' % self.server.server_port, timeout_seconds=5)
        self.client = ChatClient('synthetic-key')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def generate(self):
        return self.client.generate(load_instructions(), json.dumps(self.fixture['record']), self.settings, 0)

    def test_pipeline_and_wire_format(self):
        result = process_request({'contract_version': '0.1', 'request_id': 'test-chat', 'records': [self.fixture['record']]},
                                 ExtractionAdapter(self.client), self.settings)
        self.assertEqual(result['status'], 'success', result['errors'])
        self.assertEqual(len(result['tasks']), 2)
        self.assertEqual(len(self.calls), 1)
        path, auth, payload = self.calls[0]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(auth, 'Bearer synthetic-key')
        self.assertEqual(payload['model'], 'deepseek/deepseek-v4.1-flash')
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertNotIn('tools', payload)
        self.assertIn('text-task-extraction', payload['messages'][0]['content'])
        self.assertEqual(json.loads(payload['messages'][1]['content'])['text'], self.fixture['record']['text'])

    def test_v02_pipeline_keeps_frontend_contract(self):
        entries = json.loads((ROOT / 'docs/examples/text-frontend-v02-drafts.json').read_text(encoding='utf-8'))
        request = json.loads((ROOT / 'docs/examples/text-frontend-v02-request.json').read_text(encoding='utf-8'))
        request['records'] = request['records'][:1]
        self.reply['choices'][0]['message']['content'] = json.dumps(entries[0]['response'])
        result = process_request(request, ExtractionAdapter(self.client), self.settings)
        self.assertEqual(result['status'], 'success', result['errors'])
        self.assertEqual(result['contract_version'], '0.2')
        self.assertTrue(result['tasks'])

    def test_http_failures_and_redirects_are_sanitized(self):
        for status, retryable in ((401, False), (429, True), (503, True), (307, False)):
            self.status = status
            self.reply = {'private-error': 'synthetic-secret-content'}
            with self.subTest(status=status), self.assertRaises(AgentError) as caught:
                self.generate()
            self.assertEqual(caught.exception.code, 'agent_unavailable')
            self.assertEqual(caught.exception.retryable, retryable)
            self.assertNotIn('synthetic-secret-content', str(caught.exception))
        self.assertEqual(len(self.calls), 4, 'No redirects or hidden retries')

    def test_incomplete_and_malformed_responses_fail(self):
        for reply in ({}, {'choices': []}, {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]},
                      {'choices': [{'finish_reason': 'stop', 'message': {'content': None}}]},
                      {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}', 'tool_calls': [{}]}}]}):
            self.reply = reply
            with self.subTest(reply=reply), self.assertRaises(AgentError) as caught:
                self.generate()
            self.assertEqual(caught.exception.code, 'invalid_agent_output')

    def test_missing_key_never_calls_network_and_timeout_is_classified(self):
        with patch('ddl_text.chat_client.build_opener') as opener:
            with self.assertRaises(AgentError):
                ChatClient('').generate('rules', '{}', self.settings, 0)
            opener.assert_not_called()
            opener.return_value.open.side_effect = URLError(socket.timeout())
            with self.assertRaises(AgentError) as caught:
                self.generate()
            self.assertEqual(caught.exception.code, 'agent_timeout')

    def test_hung_http_is_bounded_by_worker_deadline(self):
        self.delay = 2
        settings = Settings(mode='real', model=self.settings.model, endpoint=self.settings.endpoint, timeout_seconds=.8)
        started = time.monotonic()
        result = process_request({'contract_version': '0.1', 'request_id': 'slow', 'records': [self.fixture['record']]},
                                 ExtractionAdapter(self.client), settings)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['errors'][0]['code'], 'agent_timeout')
        self.assertLess(time.monotonic() - started, 2)
