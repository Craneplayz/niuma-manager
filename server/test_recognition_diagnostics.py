import json
import os
import sqlite3
import tempfile
from pathlib import Path
import unittest
from contextlib import closing
from unittest.mock import patch
from server import recognition_diagnostics as diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_failed_attempt_can_be_saved_after_rollback_without_committing_business_data(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
            with closing(sqlite3.connect(Path(folder)/'test.sqlite3')) as db:
                diagnostics.initialize(db)
                db.execute('CREATE TABLE business(value TEXT)')
                db.commit()
                with self.assertRaises(ValueError):
                    with diagnostics.capture(db,'user','collect:run') as attempt:
                        db.execute('BEGIN IMMEDIATE')
                        db.execute('INSERT INTO business VALUES(?)',('uncommitted',))
                        raise ValueError('PRIVATE_FAILURE')
                db.rollback()
                diagnostics.persist(db,'user',attempt)
                diagnostics.persist(db,'user',attempt)
                self.assertEqual(db.execute('SELECT count(*) FROM business').fetchone()[0],0)
                history=diagnostics.attempts(db,'user','collect:run')
                self.assertEqual(len(history),1)
                self.assertEqual(history[0]['events'][-1]['exception_type'],'ValueError')

    def test_reference_keeps_exact_failure_and_owner_until_retention_expires(self):
        with closing(sqlite3.connect(':memory:')) as db,patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
            diagnostics.initialize(db)
            with diagnostics.capture(db,'user','request'):
                diagnostics.event('result',status='failed',code='agent_timeout')
            error=diagnostics.reference(db,'user','request',{'code':'agent_timeout'})
            self.assertNotIn('diagnostics',error)
            original=diagnostics.hydrate(db,'user',error)['diagnostics']
            with diagnostics.capture(db,'user','request'): diagnostics.event('result',status='success')
            self.assertEqual(diagnostics.hydrate(db,'user',error)['diagnostics'],original)
            self.assertNotIn('diagnostics',diagnostics.hydrate(db,'other-user',error))
            self.assertNotIn('_diagnostic_attempt',diagnostics.hydrate(db,'other-user',error))
            with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'production'}):
                self.assertEqual(diagnostics.hydrate(db,'user',error),{'code':'agent_timeout'})
            for index in range(199):
                with diagnostics.capture(db,'user','new:'+str(index)): pass
            self.assertEqual(db.execute('SELECT count(*) FROM recognition_attempt_diagnostics').fetchone()[0],200)
            self.assertNotIn('diagnostics',diagnostics.hydrate(db,'user',error))
            legacy=diagnostics.compact({'code':'agent_timeout','diagnostics':original})
            self.assertNotIn('diagnostics',legacy)
            self.assertNotIn('diagnostics',diagnostics.hydrate(db,'user',legacy))

    def test_only_test_environment_records_and_only_safe_metadata_survives(self):
        with closing(sqlite3.connect(':memory:')) as db:
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
        with closing(sqlite3.connect(':memory:')) as db,patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
            with self.assertRaises(ValueError):
                with diagnostics.capture(db,'user','request'):
                    for _ in range(205):diagnostics.event('pdf_batch',visual_pages=6)
                    raise ValueError('PRIVATE_RESPONSE')
            saved=diagnostics.attempts(db,'user','request')[0]
            self.assertIn('omitted_events',saved);self.assertNotIn('PRIVATE_RESPONSE',json.dumps(saved))
            self.assertEqual(saved['events'][-1]['exception_type'],'ValueError')
            self.assertLess(len(json.dumps(saved,ensure_ascii=False,indent=2)),65536)
