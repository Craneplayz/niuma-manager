"""Content-free email events, diagnostics, token accounting and admin aggregates."""
import os
from contextlib import closing
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server import email_telemetry, usage
telemetry = email_telemetry


class TelemetryDatabaseCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / 'usage.sqlite3')
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        usage.initialize(self.db)
        self.db.commit()

class EmailTelemetryTests(TelemetryDatabaseCase):

    def test_every_writer_closes_connections_after_success_and_failure(self):
        opened = []
        connect = sqlite3.connect
        def tracked(*args, **kwargs):
            db = connect(*args, **kwargs)
            opened.append(db)
            return db
        for failed in (False, True):
            with self.subTest(failed=failed), patch.object(email_telemetry.sqlite3, 'connect', side_effect=tracked):
                path = self.path if not failed else str(Path(self.path).parent / 'missing-directory' / 'db')
                self.assertEqual(email_telemetry.record(path,'u','queue'),not failed)
                self.assertEqual(email_telemetry.record_diagnostic(path,'u','generation','unknown'),not failed)
                self.assertEqual(email_telemetry.record_tokens(path,'u',{}),not failed)
        with patch.object(email_telemetry.sqlite3, 'connect', side_effect=tracked), \
             patch.object(usage, 'record', side_effect=ValueError('synthetic write failure')):
            self.assertFalse(email_telemetry.record(self.path,'u','queue'))
        self.assertEqual(len(opened),4)
        for db in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute('SELECT 1')


    def rows(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute('SELECT actor,event,success,duration_ms,quantity FROM usage_events').fetchall()

    def test_stage_whitelist_and_content_free_schema(self):
        for stage in email_telemetry.STAGES:
            self.assertTrue(email_telemetry.record(self.path, 'mail-user', stage, quantity=3))
        self.assertFalse(email_telemetry.record(self.path, 'mail-user', 'email_fetch', quantity=4))
        rows = self.rows()
        self.assertEqual(len(rows), len(email_telemetry.STAGES))
        self.assertEqual({row[1] for row in rows}, {'email_' + s for s in email_telemetry.STAGES})
        self.assertTrue(all(row[4] == 3 for row in rows))
        self.assertEqual(next(row[2] for row in rows if row[1] == 'email_failed'), 0)
        with closing(sqlite3.connect(self.path)) as db, db:
            columns = {r[1] for r in db.execute('PRAGMA table_info(usage_events)')}
        self.assertEqual(columns, {'id','actor','occurred_at','day','event','success','duration_ms','quantity'})

    def test_measure_records_success_and_failure_elapsed_and_reraises(self):
        with email_telemetry.measure(self.path, 'u', 'fetch'):
            time.sleep(.012)
        with self.assertRaisesRegex(RuntimeError, 'private exception text'):
            with email_telemetry.measure(self.path, 'u', 'recognition'):
                time.sleep(.012)
                raise RuntimeError('private exception text')
        rows = {row[1]: row for row in self.rows()}
        self.assertEqual(rows['email_fetch'][2], 1)
        self.assertEqual(rows['email_recognition'][2], 0)
        self.assertGreaterEqual(rows['email_fetch'][3], 5)
        self.assertGreaterEqual(rows['email_recognition'][3], 5)
        self.assertEqual(rows['email_fetch'][4], 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            stored = repr(db.execute('SELECT * FROM usage_events').fetchall())
        self.assertNotIn('private exception text', stored)

    def test_telemetry_failure_does_not_escape(self):
        self.assertFalse(email_telemetry.record(self.path + '.missing', 'u', 'job'))

    def test_existing_aggregate_and_retention_contract(self):
        self.assertTrue(email_telemetry.record(self.path, 'u', 'queue', duration_ms=25, quantity=2))
        self.assertTrue(email_telemetry.record(self.path, 'u', 'queue', duration_ms=75, quantity=3))
        aggregate_db = sqlite3.connect(self.path)
        aggregate_db.row_factory = sqlite3.Row
        result = usage.summary(aggregate_db, '2000-01-01', '2999-12-31')
        aggregate_db.close()
        metric = next(m for m in result['metrics'] if m['event'] == 'email_queue')
        self.assertEqual((metric['count'], metric['quantity'], metric['medianMs'], metric['p95Ms']), (2,5,50,75))
        cutoff_time = usage.datetime.now(usage.TZ) - usage.timedelta(days=90)
        with closing(sqlite3.connect(self.path)) as db, db:
            usage.record(db, 'u', 'old_event', now=cutoff_time - usage.timedelta(days=1))
        self.assertTrue(email_telemetry.record(self.path, 'u', 'job'))
        with closing(sqlite3.connect(self.path)) as db, db:
            cutoff = cutoff_time.date().isoformat()
            self.assertFalse(db.execute('SELECT 1 FROM usage_events WHERE day<?', (cutoff,)).fetchone())

    def test_diagnostic_allowlists_privacy_retention_and_grouped_reader(self):
        sentinel = 'PRIVATE mailbox body and quoted model output'
        self.assertFalse(email_telemetry.record_diagnostic(self.path, 'user-a', sentinel, 'unknown'))
        self.assertFalse(email_telemetry.record_diagnostic(self.path, 'user-a', 'generation', sentinel))
        self.assertTrue(email_telemetry.record_diagnostic(self.path, 'user-a', 'generation', 'invalid_json', 17))
        self.assertTrue(email_telemetry.record_diagnostic(self.path, 'user-b', 'validation', 'evidence', 31))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            rows = email_telemetry.diagnostics(db, '2000-01-01', '2999-12-31')
            self.assertEqual({(r['stage'], r['reason'], r['count']) for r in rows},
                             {('generation', 'invalid_json', 1), ('validation', 'evidence', 1)})
            self.assertEqual(email_telemetry.diagnostics(db, '2000-01-01', '2999-12-31',
                (usage.actor('user-a'),)), [dict(stage='validation', reason='evidence', count=1, avg_duration_ms=31.0)])
            raw = repr(db.execute(f'SELECT * FROM {email_telemetry.DIAGNOSTIC_TABLE}').fetchall())
            self.assertNotIn(sentinel, raw)
            self.assertNotIn('user-a', raw)
            columns = {r[1] for r in db.execute(f'PRAGMA table_info({email_telemetry.DIAGNOSTIC_TABLE})')}
            self.assertEqual(columns, {'id','actor','stage','reason','duration_ms','created_at'})
            old = (usage.datetime.now(usage.TZ) - usage.timedelta(days=91)).isoformat()
            db.execute(f'INSERT INTO {email_telemetry.DIAGNOSTIC_TABLE} VALUES(?,?,?,?,?,?)',
                       ('old-id', usage.actor('old'), 'generation', 'unknown', 1, old))
        email_telemetry.record_diagnostic(self.path, 'user-a', 'generation', 'unknown')
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertFalse(db.execute(f'SELECT 1 FROM {email_telemetry.DIAGNOSTIC_TABLE} WHERE id=?', ('old-id',)).fetchone())


class TokenUsageTests(TelemetryDatabaseCase):

    def summary(self,start='2000-01-01',end='2999-12-31'):
        return telemetry.token_summary(self.db,start,end)

    def test_success_failure_truncation_missing_and_zero_coverage(self):
        self.assertTrue(telemetry.record_tokens(self.path,'owner',dict(prompt_tokens=100,completion_tokens=20,total_tokens=120,reasoning_tokens=5,finish_reason='stop')))
        self.assertTrue(telemetry.record_tokens(self.path,'owner',dict(prompt_tokens=100,completion_tokens=8192,total_tokens=8292,reasoning_tokens=8192,finish_reason='length'),success=False))
        self.assertTrue(telemetry.record_tokens(self.path,'owner',{},success=False))
        self.assertTrue(telemetry.record_tokens(self.path,'owner',dict(prompt_tokens=0,completion_tokens=0,total_tokens=0)))
        s=self.summary();self.assertEqual((s['attempts'],s['failed'],s['truncated']),(4,2,1))
        self.assertEqual(s['tokens']['total_tokens'],dict(total=8412,reportedAttempts=3))
        self.assertEqual(s['tokens']['reasoning_tokens'],dict(total=8197,reportedAttempts=2))
        self.assertEqual(self.summary('2000-01-01','2000-01-02')['attempts'],0)

    def test_admin_exclusions_and_no_private_fields(self):
        for uid in ('owner','admin','excluded'):
            telemetry.record_tokens(self.path,uid,dict(prompt_tokens=10,content='PRIVATE_SENTINEL',reasoning_content='PRIVATE_SENTINEL'))
        with patch.dict(os.environ,NIUMA_ADMIN_USER_IDS='admin',NIUMA_ANALYTICS_EXCLUDED_USER_IDS='excluded'):
            s=usage.summary(self.db,'2000-01-01','2999-12-31')['emailTokens']
        self.assertEqual(s['attempts'],1)
        self.assertEqual(s['tokens']['prompt_tokens']['total'],10)
        self.assertIsNone(s['tokens']['completion_tokens']['total'])
        stored=repr(self.db.execute('SELECT * FROM email_model_token_usage').fetchall())
        self.assertNotIn('PRIVATE_SENTINEL',stored)
        self.assertNotIn('actor',repr(s))

    def test_invalid_values_rejected_and_missing_table_does_not_mutate(self):
        self.assertEqual(self.summary()['attempts'],0)
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='email_model_token_usage'").fetchone())
        for value in (-1,True,1.5,'PRIVATE_SENTINEL',2**53):
            self.assertFalse(telemetry.record_tokens(self.path,'owner',dict(prompt_tokens=value)))
        self.assertFalse(telemetry.record_tokens(self.path,'owner',dict(finish_reason='PRIVATE_SENTINEL')))
        self.assertEqual(self.summary()['attempts'],0)

    def test_retention_and_locked_database_are_best_effort(self):
        telemetry.record_tokens(self.path,'owner',dict(total_tokens=8))
        self.db.execute("UPDATE email_model_token_usage SET created_at='2000-01-01'");self.db.commit()
        telemetry.record_tokens(self.path,'owner',dict(total_tokens=4))
        self.assertEqual(self.summary()['tokens']['total_tokens']['total'],4)
        self.db.execute('BEGIN EXCLUSIVE')
        self.assertFalse(telemetry.record_tokens(self.path,'owner',dict(total_tokens=4)))
        self.db.rollback()


class EmailDiagnosticsSummaryTests(TelemetryDatabaseCase):
    def test_existing_database_without_diagnostics_is_readable_without_creating_table(self):
        with patch.dict(os.environ, NIUMA_ADMIN_USER_IDS='', NIUMA_ANALYTICS_EXCLUDED_USER_IDS=''):
            self.assertEqual(usage.summary(self.db, '2000-01-01', '2999-01-01')['emailDiagnostics'], [])
        self.assertEqual(email_telemetry.diagnostics(self.db, '2000-01-01', '2999-01-01'), [])
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='email_recognition_diagnostics'").fetchone())
    def test_admin_summary_excludes_actors_and_returns_only_aggregate_categories(self):
        for uid in ('synthetic-owner', 'excluded-owner', 'admin-owner'):
            self.assertTrue(email_telemetry.record_diagnostic(self.path, uid, 'validation', 'evidence', 12))
        with patch.dict(os.environ, NIUMA_ADMIN_USER_IDS='admin-owner', NIUMA_ANALYTICS_EXCLUDED_USER_IDS='excluded-owner'):
            data = usage.summary(self.db, '2000-01-01', '2999-01-01')
        self.assertEqual(data['emailDiagnostics'], [{'stage':'validation', 'reason':'evidence', 'count':1, 'avg_duration_ms':12}])
        self.assertFalse(any(key in repr(data['emailDiagnostics']) for key in ('actor', 'synthetic-owner', 'created_at')))
        self.assertEqual(email_telemetry.diagnostics(self.db, '2000-01-01', '2000-01-02'), [])
        self.assertIn('并发阶段累计时间不等于任务墙钟时间', data['methodology'])


if __name__ == "__main__":
    unittest.main()
