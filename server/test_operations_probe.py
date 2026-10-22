"""Temporary databases, synthetic /proc and SMTP substitutes only."""
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from server import operations as ops
from server import operations_probe as probe


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.environment('test')
        self.now = 1800000000
        self.resource = dict(cpuPercent=12, memoryUsedBytes=100, memoryTotalBytes=200,
                             diskUsedBytes=2 * 1024 ** 3, diskTotalBytes=10 * 1024 ** 3,
                             restarts={'api': 0, 'web': 1}, available=True, serviceAvailable=True)
        self.notices = []

    def environment(self, name):
        directory = self.root / name
        directory.mkdir()
        (directory / '.niuma-environment.json').write_text(json.dumps(dict(environment=name)), encoding='utf-8')
        path = directory / 'business.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE source_sync_runs(state TEXT,snapshot TEXT)')
        ops.initialize(path)
        return path

    def send(self, *notice):
        self.notices.append(notice)

    def tick(self, seconds=0, sender=None, resource=None, path=None, environment='test'):
        return probe.run_once(path or self.path, environment,
                              self.resource if resource is None else resource,
                              self.send if sender is None else sender, now=self.now + seconds)

    def alert(self, code='service_unavailable'):
        with ops.connection(self.path) as db:
            row = db.execute('SELECT * FROM alerts WHERE code=?', (code,)).fetchone()
            return dict(row) if row else None

    def run_record(self, state, age, **private):
        stamp = datetime.fromtimestamp(self.now - age, timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO source_sync_runs VALUES(?,?)',
                       (state, json.dumps(dict(created_at=stamp, updated_at=stamp, **private))))
        if state not in ('queued', 'crawling', 'recognizing'):
            with ops.scope(self.path, operation='sync'):
                ops.emit('run', code=state, result='failed' if state == 'failed' else 'ok', at=self.now - age)

    def test_duration_dedup_repeat_and_recovery(self):
        self.resource['serviceAvailable'] = False
        for at in (0, 60, 119):
            self.assertEqual(self.tick(at), 0)
        self.assertEqual(self.notices, [])
        self.tick(120)
        for at in range(180, 1920, 60):
            self.tick(at)
        self.assertEqual(len(self.notices), 1)
        self.tick(1920)
        self.assertEqual(len(self.notices), 2)
        self.resource['serviceAvailable'] = True
        self.tick(1980)
        self.tick(2040)
        self.assertEqual(self.notices[-1], ('test', 'service_unavailable', 'recovered'))
        self.assertEqual(len(self.notices), 3)
        self.assertEqual(self.alert()['state'], 'resolved')
        self.assertIsNone(self.alert()['active_since'])
        self.resource['serviceAvailable'] = False
        for at in (2100, 2160, 2220):
            self.tick(at)
        self.assertEqual(len(self.notices), 4)

    def test_failed_delivery_and_recovery_retry_survive_processes(self):
        self.resource['serviceAvailable'] = False
        self.tick()
        fail = Mock(side_effect=RuntimeError('PASSWORD SESSION BODY'))
        self.assertEqual(self.tick(120, sender=fail), 1)
        self.assertEqual(self.alert()['delivery_error'], 1)
        self.assertIsNone(self.alert()['last_notified'])
        self.assertEqual(self.tick(180), 0)
        self.assertEqual(self.alert()['last_notified'], self.now + 180)
        self.resource['serviceAvailable'] = True
        self.assertEqual(self.tick(240, sender=fail), 1)
        self.assertEqual(self.alert()['state'], 'recovering')
        self.assertEqual(self.tick(300), 0)
        self.assertEqual(self.notices[-1][-1], 'recovered')
        self.assertEqual(self.alert()['delivery_error'], 0)

    def test_sample_only_preserves_pending_notices_without_claiming_delivery(self):
        self.resource['serviceAvailable']=False
        self.assertEqual(probe.run_once(self.path,'test',self.resource,None,now=self.now),0)
        self.assertEqual(probe.run_once(self.path,'test',self.resource,None,now=self.now+120),0)
        self.assertEqual(self.alert()['state'],'firing')
        self.assertIsNone(self.alert()['last_notified'])
        self.assertEqual(self.alert()['delivery_error'],0)
        self.tick(180)
        self.assertEqual(self.notices,[('test','service_unavailable','firing')])

    def test_failed_reminder_retries_before_repeat_interval(self):
        self.resource['serviceAvailable'] = False
        self.tick()
        self.tick(120)
        self.tick(1920, sender=lambda *args: False)
        self.assertEqual(self.alert()['delivery_error'], 1)
        self.tick(1980)
        self.assertEqual(len(self.notices), 2)
        self.assertEqual(self.alert()['delivery_error'], 0)

    def test_recovery_after_unsent_firing_is_not_lost(self):
        self.resource['serviceAvailable'] = False
        self.tick()
        self.tick(120, sender=lambda *args: False)
        self.resource['serviceAvailable'] = True
        self.tick(180)
        self.assertEqual(self.notices, [('test', 'service_unavailable', 'recovered')])

    def test_short_incident_no_notice_and_missing_metrics_no_recovery(self):
        self.resource['serviceAvailable'] = False
        self.tick()
        self.resource['serviceAvailable'] = True
        self.tick(60)
        self.assertEqual(self.notices, [])
        self.resource['serviceAvailable'] = False
        self.tick(120)
        self.tick(180, resource={})
        self.tick(240)
        self.assertEqual(self.notices, [])
        self.tick(300)
        self.assertEqual(self.notices, [])
        self.tick(360)
        self.assertEqual(len(self.notices), 1)
        self.tick(420, resource={})
        self.assertEqual(self.alert()['state'], 'active')
        self.assertEqual(len(self.notices), 1)

    def test_sampling_gap_breaks_pending_duration(self):
        self.resource['serviceAvailable'] = False
        self.tick()
        self.tick(600)
        self.assertEqual(self.notices, [])
        self.tick(660)
        self.tick(720)
        self.assertEqual(len(self.notices), 1)

    def test_import_failures_window_all_failed_and_recovery(self):
        self.run_record('failed', 60)
        self.run_record('failed', 120)
        self.tick()
        self.assertEqual(self.notices, [])
        self.run_record('failed', 180)
        self.tick()
        self.assertEqual(self.notices, [('test', 'import_failures', 'firing')])
        self.tick(60)
        self.assertEqual(len(self.notices), 1)
        self.run_record('partial_success', 0)
        self.tick(120)
        self.assertEqual(self.notices[-1][-1], 'recovered')
        self.tick(1000)
        self.assertEqual(len(self.notices), 2)

    def test_old_failures_and_active_runs_do_not_count(self):
        for state, age in [('failed', 601), ('failed', 602), ('failed', 603), ('crawling', 20)]:
            self.run_record(state, age)
        self.assertFalse(probe.business_conditions(self.path, self.now)['import_failures'])

    def test_record_events_excluded_from_run_failures(self):
        with ops.scope(self.path, operation='sync.record'):
            for _ in range(3):
                ops.emit('run', code='failed', result='failed', at=self.now)
        self.assertFalse(probe.business_conditions(self.path, self.now)['import_failures'])
        for _ in range(3):
            self.run_record('failed', 0)
        with ops.scope(self.path, operation='sync.record'):
            ops.emit('run', code='succeeded', at=self.now)
        self.assertTrue(probe.business_conditions(self.path, self.now)['import_failures'])

    def test_all_import_operations_count_only_terminal_events(self):
        for operation in ('sync', 'materials', 'reprocessing', 'public'):
            with self.subTest(operation=operation):
                with ops.connection(self.path) as db:
                    db.execute('DELETE FROM events')
                with ops.scope(self.path, operation=operation):
                    for _ in range(2):
                        ops.emit('run', code='failed', result='failed', at=self.now)
                with ops.connection(self.path) as db:
                    # Direct insert also tests exclusion before producers add
                    # queued/running to their own event-code allowlist.
                    for code in ('queued', 'running'):
                        db.execute("""INSERT INTO events(at,request_id,run_id,stage,operation,
                            result,code,duration_ms,quantity) VALUES(?, '', '', 'run', ?, 'ok', ?, 0, 1)""",
                                   (self.now, operation, code))
                self.assertFalse(probe.business_conditions(self.path, self.now)['import_failures'])
                with ops.scope(self.path, operation=operation):
                    ops.emit('run', code='failed', result='failed', at=self.now)
                self.assertTrue(probe.business_conditions(self.path, self.now)['import_failures'])
                for code in ('succeeded', 'partial_success', 'stopped', 'interrupted'):
                    with ops.scope(self.path, operation=operation):
                        ops.emit('run', code=code, at=self.now)
                    self.assertFalse(probe.business_conditions(self.path, self.now)['import_failures'])
                    with ops.connection(self.path) as db:
                        db.execute('DELETE FROM events WHERE code=?', (code,))

    def test_mixed_import_operations_aggregate_failures(self):
        for operation in ('materials', 'reprocessing', 'public'):
            with ops.scope(self.path, operation=operation):
                ops.emit('run', code='failed', result='failed', at=self.now)
        self.tick()
        self.assertEqual(self.notices, [('test', 'import_failures', 'firing')])

    def test_queue_oldest_strict_threshold_and_persistence(self):
        self.run_record('queued', 30)
        self.tick()
        self.tick(1)
        self.tick(31)
        self.assertEqual(self.notices, [])
        self.tick(61)
        self.assertEqual(self.notices, [('test', 'queue_backlog', 'firing')])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE source_sync_runs SET state='succeeded'")
        self.tick(121)
        self.assertEqual(self.notices[-1][-1], 'recovered')

    def test_requeued_long_run_waits_from_updated_at(self):
        self.run_record('queued', 3600)
        updated = datetime.fromtimestamp(self.now, timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE source_sync_runs SET snapshot=json_set(snapshot,'$.updated_at',?)", (updated,))
        for at in (0, 30):
            self.assertFalse(probe.business_conditions(self.path, self.now + at)['queue_backlog'])
            self.tick(at)
        self.assertEqual(self.notices, [])
        self.assertIsNone(self.alert('queue_backlog'))
        for at in (31, 61, 91):
            self.tick(at)
        self.assertEqual(self.notices, [('test', 'queue_backlog', 'firing')])

    def test_queue_notice_precedes_admission_expiry_with_minute_sampling(self):
        import source_jobs
        self.run_record('queued',0)
        for at in (0,60,120):self.tick(at)
        self.assertEqual(self.notices,[('test','queue_backlog','firing')])
        self.assertLess(120,source_jobs.QUEUE_SECONDS)

    def test_all_persistent_import_queues_trigger_and_recover(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE reprocessing_runs(snapshot TEXT)')
            db.execute('CREATE TABLE public_source_runs(state TEXT,created_at TEXT)')
            db.execute('CREATE TABLE manual_material_jobs(job_id TEXT)')
            db.execute('CREATE TABLE wechat_jobs(id TEXT,status TEXT)')
        stamp=datetime.fromtimestamp(self.now-121,timezone.utc).isoformat()
        for kind in ('reprocessing','public','materials'):
            with self.subTest(kind=kind):
                with closing(sqlite3.connect(self.path)) as db, db:
                    if kind=='reprocessing':db.execute('INSERT INTO reprocessing_runs VALUES(?)',(json.dumps(dict(state='queued',created_at=stamp,updated_at=stamp,private='PRIVATE')),))
                    elif kind=='public':db.execute("INSERT INTO public_source_runs VALUES('queued',?)",(stamp,))
                    else:
                        db.execute("INSERT INTO wechat_jobs VALUES('private-material','queued')")
                        db.execute("INSERT INTO manual_material_jobs VALUES('private-material')")
                if kind=='materials':
                    with ops.scope(self.path,run_id='private-material',operation='materials'):
                        ops.emit('run',code='queued',at=self.now-121)
                self.assertTrue(probe.business_conditions(self.path,self.now)['queue_backlog'])
                self.tick();self.tick(120)
                self.assertEqual(self.notices[-1],('test','queue_backlog','firing'))
                with closing(sqlite3.connect(self.path)) as db, db:
                    for table in ('reprocessing_runs','public_source_runs','wechat_jobs','manual_material_jobs'):
                        db.execute('DELETE FROM '+table)
                self.tick(180)
                self.assertEqual(self.notices[-1],('test','queue_backlog','recovered'))
                with ops.connection(self.path) as db:db.execute('DELETE FROM alerts')

    def test_unobserved_material_wait_is_unknown_not_a_false_recovery(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE manual_material_jobs(job_id TEXT)')
            db.execute('CREATE TABLE wechat_jobs(id TEXT,status TEXT)')
            db.execute("INSERT INTO manual_material_jobs VALUES('unknown')")
            db.execute("INSERT INTO wechat_jobs VALUES('unknown','queued')")
        self.assertIsNone(probe.business_conditions(self.path,self.now)['queue_backlog'])
        self.run_record('queued',121)
        self.assertTrue(probe.business_conditions(self.path,self.now)['queue_backlog'])
        self.tick();self.tick(120)
        with closing(sqlite3.connect(self.path)) as db, db:db.execute('DELETE FROM source_sync_runs')
        self.tick(180)
        self.assertEqual(self.notices,[('test','queue_backlog','firing')])

    def test_queue_timestamp_falls_back_to_created_at(self):
        self.run_record('queued', 121)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE source_sync_runs SET snapshot=json_remove(snapshot,'$.updated_at')")
        self.assertTrue(probe.business_conditions(self.path, self.now)['queue_backlog'])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE source_sync_runs SET snapshot=json_set(snapshot,'$.updated_at',NULL)")
        self.assertTrue(probe.business_conditions(self.path, self.now)['queue_backlog'])

    def test_disk_both_thresholds_and_recovery(self):
        for total, used in [(100 * 1024 ** 3, 91 * 1024 ** 3), (2 * 1024 ** 3, 1.1 * 1024 ** 3)]:
            self.resource.update(diskTotalBytes=total, diskUsedBytes=used)
            self.assertTrue(probe.conditions(self.resource, {})['disk_low'])
        self.resource.update(diskTotalBytes=10 * 1024 ** 3, diskUsedBytes=9 * 1024 ** 3)
        self.assertFalse(probe.conditions(self.resource, {})['disk_low'])
        self.resource['diskUsedBytes'] += 1
        self.tick()
        self.tick(60)
        self.tick(120)
        self.assertEqual(self.notices[-1][1], 'disk_low')
        self.resource['diskUsedBytes'] = 0
        self.tick(180)
        self.assertEqual(self.notices[-1][-1], 'recovered')

    def test_environment_isolation_rejects_wrong_marker_and_uninitialized_sidecar(self):
        production = self.environment('production')
        self.resource['serviceAvailable'] = False
        self.tick()
        self.tick(120)
        self.tick(path=production, environment='production')
        with ops.connection(production) as db:
            self.assertIsNone(db.execute('SELECT last_notified FROM alerts').fetchone()[0])
        with self.assertRaises(probe.ProbeError):
            self.tick(path=production)
        ops.database(production).unlink()
        with self.assertRaises(probe.ProbeError):
            self.tick(path=production, environment='production')
        self.assertFalse(ops.database(production).exists())

    def test_privacy_projection_and_notice_failure_no_private_output(self):
        self.run_record('failed', 1, password='PRIVATE-PASSWORD', session='PRIVATE-SESSION', body='PRIVATE-BODY')
        self.run_record('failed', 2)
        self.run_record('failed', 3)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(self.tick(sender=Mock(side_effect=ValueError('PRIVATE-EXCEPTION'))), 1)
        with ops.connection(self.path) as db:
            persisted = str([tuple(row) for row in db.execute('SELECT * FROM resources')])
            persisted += str([tuple(row) for row in db.execute('SELECT * FROM alerts')])
        self.assertNotIn('PRIVATE', output.getvalue() + persisted)
        self.assertNotIn('snapshot', persisted)

    def test_unknown_business_data_no_false_recovery_or_empty_database_creation(self):
        missing = self.root / 'missing.db'
        self.assertEqual(probe.business_conditions(missing, self.now), dict(import_failures=None, queue_backlog=None))
        self.assertFalse(missing.exists())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO source_sync_runs VALUES('queued','not-json')")
        self.assertEqual(probe.business_conditions(self.path, self.now), dict(import_failures=False, queue_backlog=None))

    def test_proc_fixture_cpu_memory_and_missing_proc(self):
        proc = self.root / 'proc'
        proc.mkdir()
        (proc / 'stat').write_text('cpu 10 0 10 80 0 0 0 0 2 0\n', encoding='utf-8')
        (proc / 'meminfo').write_text('MemTotal: 100 kB\nMemAvailable: 40 kB\n', encoding='utf-8')
        def update(_):
            (proc / 'stat').write_text('cpu 20 0 20 100 0 0 0 0 2 0\n', encoding='utf-8')
        result = probe.host_resources(self.root, proc=proc, pause=update)
        self.assertEqual(result['cpuPercent'], 50)
        self.assertEqual(result['memoryUsedBytes'], 60 * 1024)
        self.assertEqual(result['memoryTotalBytes'], 100 * 1024)
        missing = probe.host_resources(self.root, proc=self.root / 'absent')
        self.assertIsNone(missing['cpuPercent'])
        self.assertIsNone(missing['memoryUsedBytes'])
        self.assertGreater(missing['diskTotalBytes'], 0)

    def test_native_host_sampling(self):
        result = probe.host_resources(self.root)
        self.assertGreater(result['diskTotalBytes'], 0)
        if sys.platform.startswith('linux'):
            self.assertIsNotNone(result['cpuPercent'])
            self.assertGreater(result['memoryTotalBytes'], 0)
        else:
            self.assertIsNone(result['cpuPercent'])
            self.assertIsNone(result['memoryTotalBytes'])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Real /proc requires a Linux host')
    def test_linux_proc_real(self):
        result = probe.host_resources(self.root)
        self.assertIsNotNone(result['cpuPercent'])
        self.assertGreaterEqual(result['cpuPercent'], 0)
        self.assertLessEqual(result['cpuPercent'], 100)
        self.assertGreater(result['memoryTotalBytes'], 0)
        self.assertLessEqual(result['memoryUsedBytes'], result['memoryTotalBytes'])

    def test_docker_label_scope_and_minimal_inspection(self):
        calls = []
        def execute(argv, **kwargs):
            calls.append(argv)
            self.assertNotIn('shell', kwargs)
            return SimpleNamespace(stdout='a' * 12 + '\n' if argv[1] == 'ps' else '3 true\n')
        self.assertEqual(probe.docker_restarts('test-project', execute=execute), ({'api': 3, 'web': 3}, True))
        self.assertIn('label=com.docker.compose.project=test-project', calls[0])
        self.assertIn('label=com.docker.compose.service=web', calls[2])
        self.assertEqual(calls[1][3], '{{.RestartCount}} {{.State.Running}}')
        self.assertEqual(probe.docker_restarts('test', execute=Mock(side_effect=FileNotFoundError())), (None, None))
        self.assertEqual(probe.docker_restarts('test', execute=Mock(return_value=SimpleNamespace(stdout=''))),
                         ({'api': None, 'web': None}, False))

    def test_inherited_image_labels_and_oneoff_containers_are_not_services(self):
        calls = []
        def execute(argv, **kwargs):
            calls.append(argv)
            if argv[1] == 'ps':
                scoped = ('label=com.docker.compose.config-hash' in argv and
                          'label=com.docker.compose.oneoff=False' in argv)
                return SimpleNamespace(stdout='a' * 12 + '\n' + ('' if scoped else 'b' * 12 + '\n'))
            return SimpleNamespace(stdout=''.join('3 true\n' if value == 'a' * 12 else '9 false\n'
                                                  for value in argv[4:]))
        self.assertEqual(probe.docker_restarts('test-project', execute=execute), ({'api': 3, 'web': 3}, True))
        self.assertTrue(all('b' * 12 not in argv for argv in calls if argv[1] == 'inspect'))

    def test_http_failure_still_detected_without_docker(self):
        with patch.object(probe, 'host_resources', return_value=dict(self.resource)), \
                patch.object(probe, 'docker_restarts', return_value=(None, None)), \
                patch.object(probe, 'http_available', return_value=False):
            result = probe.sample(self.root, 'test', 'http://localhost/api/health')
            self.assertTrue(result['available'])
            self.assertFalse(result['serviceAvailable'])
        with patch.object(probe, 'host_resources', return_value=dict(self.resource)), \
                patch.object(probe, 'docker_restarts', return_value=(None, None)), \
                patch.object(probe, 'http_available', return_value=True):
            result = probe.sample(self.root, 'test', 'http://localhost/api/health')
            self.assertTrue(result['available'])
            self.assertIsNone(result['serviceAvailable'])

    def test_sample_restart_sum_or_null(self):
        with patch.object(probe, 'host_resources', side_effect=lambda _: dict(self.resource)), \
                patch.object(probe, 'http_available', return_value=True):
            for counts, expected in [({'api': 2, 'web': 3}, 5), ({'api': 2, 'web': None}, None), (None, None)]:
                with patch.object(probe, 'docker_restarts', return_value=(counts, True)):
                    self.assertEqual(probe.sample(self.root, 'test', 'http://localhost/api/health')['restarts'], expected)

    def test_smtp_reuses_shared_quota_before_network_and_tls(self):
        order = []
        quota = SimpleNamespace(configuration=Mock(return_value=('shared', 10, 100)),
                                reserve=Mock(side_effect=lambda settings: order.append('reserve')))
        delivery = SimpleNamespace(mail_budget=quota, _address=lambda address: True,
                                   _configuration=lambda values: ('smtp.invalid', 465, 'sender@example.invalid', 'PRIVATE', 3))
        smtp = MagicMock()
        smtp.return_value.__enter__.return_value.send_message.return_value = {}
        smtp.side_effect = lambda *a, **k: (order.append('connect') or smtp.return_value)
        config = dict(mail={}, recipient='ops@example.invalid', smtp_tls='implicit')
        with patch.object(probe.importlib, 'import_module', return_value=delivery), patch.object(probe.smtplib, 'SMTP_SSL', smtp):
            probe.smtp_notifier(config)('test', 'disk_low', 'firing')
        self.assertEqual(order, ['reserve', 'connect'])
        quota.configuration.assert_called_once_with(config['mail'])
        quota.reserve.assert_called_once_with(('shared', 10, 100))
        self.assertIn('context', smtp.call_args.kwargs)
        message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertNotIn('PRIVATE', message.as_string())
        self.assertEqual(message['To'],config['recipient'])
        self.assertEqual(smtp.return_value.__enter__.return_value.send_message.call_args.kwargs['to_addrs'],[config['recipient']])

    def test_smtp_missing_dependency_budget_or_tls_safely_fails_before_network(self):
        config = dict(mail={}, recipient='ops@example.invalid', smtp_tls='implicit')
        with patch.object(probe.importlib, 'import_module', side_effect=ModuleNotFoundError()), \
                patch.object(probe.smtplib, 'SMTP_SSL') as smtp:
            with self.assertRaisesRegex(probe.ProbeError, '^notice_delivery_unavailable$'):
                probe.smtp_notifier(config)('test', 'disk_low', 'firing')
            smtp.assert_not_called()
        for settings, tls in [(None, 'implicit'), (('shared', 10, 100), 'none')]:
            config['smtp_tls'] = tls
            delivery = SimpleNamespace(mail_budget=SimpleNamespace(configuration=lambda _: settings, reserve=Mock()))
            with patch.object(probe.importlib, 'import_module', return_value=delivery), patch.object(probe.smtplib, 'SMTP_SSL') as smtp:
                with self.assertRaises(probe.ProbeError):
                    probe.smtp_notifier(config)('test', 'disk_low', 'firing')
                delivery.mail_budget.reserve.assert_not_called()
                smtp.assert_not_called()

    def test_quota_exhaustion_smtp_refusal_and_network_failures_retry(self):
        quota = SimpleNamespace(configuration=lambda _: ('shared', 10, 100), reserve=Mock())
        delivery = SimpleNamespace(mail_budget=quota, _address=lambda _: True,
                                   _configuration=lambda _: ('smtp.invalid', 465, 'ops@example.invalid', 'PRIVATE', 3))
        config = dict(mail={}, recipient='ops@example.invalid', smtp_tls='implicit')
        with patch.object(probe.importlib, 'import_module', return_value=delivery), patch.object(probe.smtplib, 'SMTP_SSL') as smtp:
            quota.reserve.side_effect = ValueError('PRIVATE-QUOTA')
            with self.assertRaises(probe.ProbeError):
                probe.smtp_notifier(config)('test', 'disk_low', 'firing')
            smtp.assert_not_called()
            quota.reserve.side_effect = None
            smtp.return_value.__enter__.return_value.send_message.return_value = {'ops@example.invalid': 'PRIVATE'}
            with self.assertRaises(probe.ProbeError):
                probe.smtp_notifier(config)('test', 'disk_low', 'firing')
            smtp.side_effect = OSError('PRIVATE-NETWORK')
            with self.assertRaises(probe.ProbeError):
                probe.smtp_notifier(config)('test', 'disk_low', 'firing')
            self.assertEqual(quota.reserve.call_count, 3)

    def config_file(self):
        config = self.root / 'probe.json'
        config.write_text(json.dumps(dict(database=str(self.path), environment='test', disk_path=str(self.root),
                                         compose_project='niuma-test', health_url='http://127.0.0.1:1331/api/health')), encoding='utf-8')
        config.chmod(0o600)
        return config

    @unittest.skipUnless(os.name == 'posix', 'POSIX private configuration')
    def test_sample_only_cli_never_constructs_mail_notifier(self):
        config=self.config_file()
        with patch.object(probe,'sample',return_value=self.resource),patch.object(probe,'smtp_notifier') as notifier:
            self.assertEqual(probe.main(['--config',str(config),'--sample-only']),0)
            notifier.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'POSIX private configuration')
    def test_private_config_permissions_and_secret_free_cli_errors(self):
        config = self.config_file()
        self.assertEqual(probe.load_config(config)['environment'], 'test')
        config.chmod(0o644)
        with self.assertRaises(probe.ProbeError):
            probe.load_config(config)
        config.chmod(0o600)
        output = io.StringIO()
        with patch.object(probe, 'sample', side_effect=ValueError('PRIVATE-PASSWORD SESSION BODY')), redirect_stderr(output):
            self.assertEqual(probe.main(['--config', str(config)]), 1)
        self.assertEqual(output.getvalue(), 'operations_probe: sampling_or_configuration_failed\n')

    def test_windows_cli_reports_fixed_unsupported_platform(self):
        with patch.object(probe.os, 'name', 'nt'):
            with self.assertRaisesRegex(probe.ProbeError, '^unsupported_platform$'):
                probe.load_config('not-opened.json')

    @unittest.skipUnless(os.name == 'posix', 'POSIX flock')
    def test_cli_nonoverlap_skips_sampling(self):
        import fcntl
        config = self.config_file()
        with Path(str(ops.database(self.path)) + '.probe.lock').open('a', encoding='utf-8') as lock, patch.object(probe, 'sample') as sample:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(probe.main(['--config', str(config)]), 0)
            sample.assert_not_called()


if __name__ == '__main__':
    unittest.main()
