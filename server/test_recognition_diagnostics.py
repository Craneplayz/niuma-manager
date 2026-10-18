import json
import os
import sqlite3
import unittest
from unittest.mock import patch
from server import recognition_diagnostics as diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_only_test_environment_records_and_only_safe_metadata_survives(self):
        with sqlite3.connect(':memory:') as db:
            diagnostics.initialize(db)
            for env in ('production','development','test'):
                with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':env}):
                    with diagnostics.capture(db,'user','request'):
                        diagnostics.event('pdf_batch',page_start=7,visual_pages=3,timeout_seconds=60,
                                          secret='PRIVATE',body='COURSE_BODY',url='TOKEN_URL',
                                          schema={'schema_path':'properties/tasks/items/required','rule':'required'})
                    out=diagnostics.attempts(db,'user','request')
                    self.assertEqual(len(out),1 if env=='test' else 0)
            stored=db.execute('SELECT detail FROM recognition_attempt_diagnostics').fetchone()[0]
            self.assertNotIn('PRIVATE',stored);self.assertNotIn('COURSE_BODY',stored);self.assertNotIn('TOKEN_URL',stored)
            with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
                self.assertEqual(diagnostics.attempts(db,'other','request'),[])
                self.assertIn('diagnostics',diagnostics.attach(db,'user','request',{'code':'agent_timeout'}))
            with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'production'}):
                self.assertEqual(diagnostics.attach(db,'user','request',{'code':'agent_timeout'}),{'code':'agent_timeout'})

    def test_failure_is_retained_without_exception_text_and_trace_is_bounded(self):
        with sqlite3.connect(':memory:') as db,patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
            with self.assertRaises(ValueError):
                with diagnostics.capture(db,'user','request'):
                    for _ in range(205):diagnostics.event('pdf_batch',visual_pages=6)
                    raise ValueError('PRIVATE_RESPONSE')
            saved=diagnostics.attempts(db,'user','request')[0]
            self.assertIn('omitted_events',saved);self.assertNotIn('PRIVATE_RESPONSE',json.dumps(saved))
            self.assertEqual(saved['events'][-1]['exception_type'],'ValueError')
            self.assertLess(len(json.dumps(saved,ensure_ascii=False,indent=2)),65536)
