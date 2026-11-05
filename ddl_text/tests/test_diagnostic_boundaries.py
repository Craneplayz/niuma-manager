"""Fault injection through real worker/HTTP boundaries, not model accuracy tests."""
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ddl_text import diagnostics
from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.chat_client import ChatClient
from ddl_text.extraction import ExtractionAdapter
from ddl_text.pipeline import process_request
from ddl_text.media_worker import bounded
from ddl_text.runner import run_attempt
from ddl_text.tests.test_recognition_errors import IndexedClient


def crash():
    diagnostics.emit('decode', status='started')
    raise RuntimeError('PRIVATE-CONTENT-AND-SECRET')


def die():
    diagnostics.emit('decode', status='started')
    os._exit(23)


def hang():
    diagnostics.emit('decode', status='started')
    time.sleep(20)


def many_events():
    for _ in range(250): diagnostics.emit('decode',status='started')
    raise RuntimeError('PRIVATE-CONTENT')


class FaultAdapter:
    def __init__(self, function): self.function = function
    def extract(self, *args): return self.function()


class DiagnosticBoundaries(unittest.TestCase):
    def test_worker_event_cap_marks_truncation_and_keeps_terminal_failure(self):
        for boundary in ('text','media'):
            events=[]
            with diagnostics.scope(lambda stage,**fields: events.append(dict(stage=stage,**fields))):
                with self.assertRaises(AgentError):
                    if boundary=='text': run_attempt(FaultAdapter(many_events),{},Settings(timeout_seconds=8),0)
                    else: bounded(many_events,(),8)
            self.assertTrue(any(e.get('status')=='truncated' for e in events))
            self.assertTrue(any(e.get('exception_type')=='RuntimeError' for e in events))
            self.assertLess(len(events),210)

    def test_unknown_crash_exit_and_timeout_survive_both_worker_protocols(self):
        for boundary in ('text', 'media'):
            for function, reason in ((crash,'internal_error'),(die,'worker_terminated'),(hang,'worker_timeout')):
                with self.subTest(boundary=boundary, reason=reason):
                    events=[]
                    with diagnostics.scope(lambda stage, **fields: events.append(dict(stage=stage, **fields))):
                        with self.assertRaises(AgentError) as caught:
                            if boundary=='text': run_attempt(FaultAdapter(function),{},Settings(timeout_seconds=2),0)
                            else: bounded(function,(),2)
                    self.assertEqual(caught.exception.diagnostic,reason)
                    self.assertTrue(any(e['stage']=='decode' for e in events))
                    self.assertTrue(any(e['stage']=='worker_exit' for e in events))
                    self.assertNotIn('PRIVATE-',json.dumps(events))
                    if function is crash:
                        failure=next(e for e in events if e.get('exception_type')=='RuntimeError')
                        self.assertTrue(any(f['function']=='crash' for f in failure['locations']))
                    if function is die: self.assertTrue(any(e.get('exit_code')==23 for e in events))

    def test_actual_text_worker_reports_both_failures_and_missing_field(self):
        events=[]
        request=dict(contract_version='0.1',request_id='matrix',records=[dict(input_id='m',text='明天上ics')])
        result=process_request(request,ExtractionAdapter(IndexedClient('persistent')),Settings(mode='real',timeout_seconds=8),
            diagnostic_event=lambda stage, **fields: events.append(dict(stage=stage, **fields)))
        self.assertEqual(result['status'],'failed')
        detail=result['errors'][0]['diagnostic']
        self.assertEqual(detail['missing_fields'],['prerequisites'])
        self.assertEqual(detail['instance_path'],'tasks/0')
        self.assertEqual([e['attempt'] for e in events if e['stage']=='model_attempt' and e.get('status')=='started'],[1,2])
        self.assertEqual([e['attempt'] for e in events if e['stage']=='validation' and e.get('diagnostic')=='draft_schema'],[1,2])

    def test_http_provider_response_failures_are_not_internal_or_validation_failures(self):
        class Handler(BaseHTTPRequestHandler):
            status=200
            body=b'{}'
            def log_message(self,*args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(type(self).status); self.end_headers(); self.wfile.write(type(self).body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            cases=[(401,b'PRIVATE-KEY','model_configuration'),(429,b'PRIVATE-KEY','model_rate_limited'),
                   (503,b'PRIVATE-KEY','model_unavailable'),(200,b'not-json','provider_response'),
                   (200,b'{"choices":[{"finish_reason":"length"}]}','output_truncated')]
            settings=Settings(endpoint=f'http://127.0.0.1:{server.server_port}',timeout_seconds=5)
            for status, body, reason in cases:
                Handler.status,Handler.body=status,body
                events=[]
                with diagnostics.scope(lambda stage,**fields: events.append(dict(stage=stage,**fields))):
                    with self.assertRaises(AgentError) as caught:
                        ChatClient('synthetic-only').complete_json('instructions','{}',settings)
                self.assertEqual(caught.exception.diagnostic,reason)
                self.assertTrue(any(e['stage']=='model_transport' for e in events))
                self.assertNotIn('PRIVATE-',json.dumps(events))
                if status!=200: self.assertTrue(any(e.get('http_status')==status for e in events))
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__=='__main__': unittest.main()
