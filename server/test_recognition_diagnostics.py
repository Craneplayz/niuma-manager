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
    def test_sqlite_full_cannot_report_success_after_business_transaction_is_lost(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
            with closing(sqlite3.connect(Path(folder)/'full.sqlite3')) as db:
                diagnostics.initialize(db)
                db.execute('CREATE TABLE business(value TEXT)')
                db.commit()
                pages = db.execute('PRAGMA page_count').fetchone()[0]
                db.execute('PRAGMA max_page_count='+str(pages))
                with self.assertLogs('recognition_diagnostics',level='ERROR'):
                    with self.assertRaises(sqlite3.OperationalError) as caught:
                        with diagnostics.capture(db,'user','full'):
                            db.execute('INSERT INTO business VALUES(?)',('fits existing page',))
                            self.assertTrue(db.in_transaction)
                            for _ in range(100):
                                diagnostics.event('pdf_batch',visual_pages=6,timeout_seconds=60,model='synthetic')
                self.assertFalse(db.in_transaction)
                self.assertEqual(db.execute('SELECT count(*) FROM business').fetchone()[0],0)
                self.assertEqual(caught.exception._recognition_failure['failure']['reason'],'save_failed')
                self.assertEqual(caught.exception._recognition_attempt['persistence_error']['code'],'SQLITE_FULL')
                # The HTTP failure handler can safely finalize it after rollback.
                db.execute('PRAGMA max_page_count=1000')
                failure = diagnostics.finalize_error(db,'user',caught.exception)
                self.assertEqual(failure['failure']['reason'],'save_failed')
                self.assertEqual(len(diagnostics.lookup(db,'user',failure['diagnostic_id'])),1)

    def test_statement_failure_rolls_back_only_diagnostics_and_keeps_business_writes(self):
        with closing(sqlite3.connect(':memory:')) as db:
            diagnostics.initialize(db)
            db.execute('CREATE TABLE business(value TEXT)')
            # RAISE(FAIL) preserves the INSERT before the trigger fails. Our
            # savepoint must undo it without undoing the caller's earlier row.
            db.execute("""CREATE TRIGGER fail_diagnostic AFTER INSERT ON recognition_attempt_diagnostics
                BEGIN SELECT RAISE(FAIL,'synthetic diagnostic failure'); END""")
            db.commit()
            with self.assertLogs('recognition_diagnostics',level='ERROR'):
                with diagnostics.capture(db,'user','statement'):
                    db.execute("INSERT INTO business VALUES('saved')")
            self.assertTrue(db.in_transaction)
            self.assertEqual(db.execute('SELECT count(*) FROM recognition_attempt_diagnostics').fetchone()[0],0)
            db.commit()
            self.assertEqual(db.execute('SELECT value FROM business').fetchone()[0],'saved')

    def test_connection_failure_during_error_response_keeps_original_diagnostic(self):
        from server import app
        with closing(sqlite3.connect(':memory:')) as db:
            try:
                with diagnostics.capture(db,'user','request'):
                    raise RuntimeError('PRIVATE-SENTINEL')
            except RuntimeError as error:
                handler=object.__new__(app.Handler);handler.usage_uid='user'
                with patch.object(app,'connection',side_effect=sqlite3.OperationalError('PRIVATE-DB')):
                    with self.assertLogs('recognition_diagnostics',level='ERROR') as logs:
                        result=handler.recognition_failure(error)
                self.assertEqual(result['failure']['reason'],'internal_error')
                self.assertEqual(result['diagnostic_id'],error._recognition_attempt['attempt_id'])
                self.assertNotIn('PRIVATE-',str(logs.output)+json.dumps(result))

    def test_persistence_failure_has_safe_log_fallback_and_does_not_replace_error(self):
        with closing(sqlite3.connect(':memory:')) as db,patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'development'}):
            diagnostics.initialize(db)
            db.execute('PRAGMA query_only=ON')
            with self.assertLogs('recognition_diagnostics',level='ERROR') as logs:
                with self.assertRaisesRegex(RuntimeError,'PRIVATE-SENTINEL') as caught:
                    with diagnostics.capture(db,'user','request'):
                        raise RuntimeError('PRIVATE-SENTINEL')
            self.assertNotIn('PRIVATE-SENTINEL',str(logs.output))
            public=caught.exception._recognition_failure
            self.assertEqual(public['failure']['reason'],'internal_error')
            self.assertEqual(json.loads(public['diagnostics'])['persistence'],'unavailable')

    def test_error_ids_survive_retry_and_lookup_is_exact_and_owner_scoped(self):
        with closing(sqlite3.connect(':memory:')) as db,patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'development'}):
            with diagnostics.capture(db,'user','request') as first:
                original=diagnostics.stamp(dict(errors=[dict(code='agent_timeout')]))
            with diagnostics.capture(db,'user','request') as second:
                diagnostics.stamp(original)
            identity=original['errors'][0]['diagnostic_id']
            self.assertEqual(identity,first['attempt_id'])
            self.assertNotEqual(identity,second['attempt_id'])
            self.assertEqual(diagnostics.lookup(db,'other',identity),[])
            self.assertEqual(diagnostics.lookup(db,'user',identity)[0]['attempt_id'],identity)
            db.execute('DELETE FROM recognition_attempt_diagnostics WHERE id=?',(identity,))
            self.assertEqual(diagnostics.lookup(db,'user',identity),[])
            self.assertNotIn('diagnostics',diagnostics.hydrate(db,'user',original['errors'][0]))

    def test_out_of_order_failures_use_exact_attempt_and_never_fall_back(self):
        with closing(sqlite3.connect(':memory:')) as db,patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
            diagnostics.initialize(db)
            with diagnostics.capture(db,'user','request') as first:
                with diagnostics.capture(db,'user','request') as later:
                    diagnostics.event('exception',exception_type='TypeError')
                diagnostics.event('exception',exception_type='ValueError')
            # Explicit order also covers equal-clock/random-ID ordering on Windows.
            db.execute('UPDATE recognition_attempt_diagnostics SET created_at=? WHERE id=?',('9999',later['attempt_id']))
            error={'code':'recognition_failed'}
            ref=diagnostics.reference(db,'user','request',error,attempt_id=first['attempt_id'])
            detail=json.loads(diagnostics.hydrate(db,'user',ref)['diagnostics'])
            self.assertEqual(detail['events'][-1]['exception_type'],'ValueError')
            self.assertEqual(diagnostics.reference(db,'user','request',error),error)
            self.assertEqual(diagnostics.reference(db,'other','request',error,attempt_id=first['attempt_id']),error)
            self.assertEqual(diagnostics.reference(db,'user','other-request',error,attempt_id=first['attempt_id']),error)
            db.execute('DELETE FROM recognition_attempt_diagnostics WHERE id=?',(first['attempt_id'],))
            self.assertEqual(diagnostics.reference(db,'user','request',error,attempt_id=first['attempt_id']),error)

    def test_batch_errors_bind_the_producing_attempt_not_the_latest(self):
        with closing(sqlite3.connect(':memory:')) as db,patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
            diagnostics.initialize(db)
            with diagnostics.capture(db,'user','request') as first:
                diagnostics.event('result',status='failed',code='invalid_agent_output')
            with diagnostics.capture(db,'user','request') as later:
                diagnostics.event('exception',exception_type='AgentError',code='agent_unavailable')
            # Force the later attempt to be the newest so a latest-guess misattributes.
            db.execute('UPDATE recognition_attempt_diagnostics SET created_at=? WHERE id=?',('9999',later['attempt_id']))
            error={'code':'invalid_agent_output','message':'识别结果未通过结构或证据校验。','retryable':True}
            bound=diagnostics.bind(db,'user','request',error,attempt_id=first['attempt_id'])
            self.assertIn('diagnostics',bound)
            self.assertIn('invalid_agent_output',bound['diagnostics'])
            self.assertNotIn('agent_unavailable',bound['diagnostics'])
            self.assertNotIn('diagnostics',diagnostics.bind(db,'user','request',error))
            self.assertNotIn('diagnostics',diagnostics.bind(db,'user','request',error,attempt_id='0'*24))

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
            with diagnostics.capture(db,'user','request') as attempt:
                diagnostics.event('result',status='failed',code='agent_timeout')
            error=diagnostics.reference(db,'user','request',{'code':'agent_timeout'},attempt_id=attempt['attempt_id'])
            self.assertNotIn('diagnostics',error)
            original=diagnostics.hydrate(db,'user',error)['diagnostics']
            with diagnostics.capture(db,'user','request'): diagnostics.event('result',status='success')
            self.assertEqual(diagnostics.hydrate(db,'user',error)['diagnostics'],original)
            self.assertNotIn('diagnostics',diagnostics.hydrate(db,'other-user',error))
            self.assertNotIn('_diagnostic_attempt',diagnostics.hydrate(db,'other-user',error))
            with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'production'}):
                self.assertEqual(diagnostics.hydrate(db,'user',error),{'code':'agent_timeout'})
            db.execute('UPDATE recognition_attempt_diagnostics SET created_at=? WHERE id=?',('2000',attempt['attempt_id']))
            for index in range(199):
                with diagnostics.capture(db,'user','new:'+str(index)): pass
            self.assertEqual(db.execute('SELECT count(*) FROM recognition_attempt_diagnostics').fetchone()[0],200)
            self.assertNotIn('diagnostics',diagnostics.hydrate(db,'user',error))
            legacy=diagnostics.compact({'code':'agent_timeout','diagnostics':original})
            self.assertNotIn('diagnostics',legacy)
            self.assertNotIn('diagnostics',diagnostics.hydrate(db,'user',legacy))

    def test_every_environment_records_safe_metadata_but_only_nonproduction_returns_details(self):
        with closing(sqlite3.connect(':memory:')) as db:
            diagnostics.initialize(db)
            for env in ('production','development','test'):
                with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':env}):
                    with diagnostics.capture(db,'user','request'):
                        diagnostics.event('pdf_batch',page_start=7,visual_pages=3,timeout_seconds=60,
                                          secret='PRIVATE',body='COURSE_BODY',url='TOKEN_URL',
                                          schema={'schema_path':'properties/tasks/items/required','rule':'required'})
                    out=diagnostics.attempts(db,'user','request')
                    self.assertEqual(len(out),0 if env=='production' else 2 if env=='development' else 3)
            stored=db.execute('SELECT detail FROM recognition_attempt_diagnostics').fetchone()[0]
            self.assertNotIn('PRIVATE',stored);self.assertNotIn('COURSE_BODY',stored);self.assertNotIn('TOKEN_URL',stored)
            with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
                self.assertEqual(diagnostics.attempts(db,'other','request'),[])
                latest=diagnostics.attempts(db,'user','request')[0]
                self.assertIn('diagnostics',diagnostics.bind(db,'user','request',{'code':'agent_timeout'},attempt_id=latest['attempt_id']))
            with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'production'}):
                self.assertEqual(diagnostics.bind(db,'user','request',{'code':'agent_timeout'},attempt_id=latest['attempt_id']),{'code':'agent_timeout'})

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
