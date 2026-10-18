import concurrent.futures
from contextlib import closing
import os
from pathlib import Path
import smtplib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import mail_budget as budget
import mail_delivery as mail


class MailBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'quota.sqlite3'
        self.values = dict(NIUMA_MAIL_BUDGET_FILE=str(self.path), NIUMA_MAIL_BUDGET_DAILY='2',
                           NIUMA_MAIL_BUDGET_TOTAL='3', NIUMA_MAIL_FROM='sender@mail.example.test',
                           NIUMA_MAIL_PASSWORD='Synthetic-private-mail-password')

    def reserve(self, **changes):
        budget.reserve(budget.configuration(dict(self.values, **changes)))

    def count(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT COUNT(*) FROM mail_attempts').fetchone()[0]

    def test_unconfigured_budget_is_backward_compatible_without_creating_files(self):
        budget.reserve(budget.configuration({}))
        self.assertFalse(self.path.exists())

    def test_partial_invalid_or_above_free_limits_fail_configuration(self):
        for changes in (dict(NIUMA_MAIL_BUDGET_TOTAL=''), dict(NIUMA_MAIL_BUDGET_FILE='relative.db'),
                        dict(NIUMA_MAIL_BUDGET_DAILY='201'), dict(NIUMA_MAIL_BUDGET_TOTAL='2001'),
                        dict(NIUMA_MAIL_BUDGET_TOTAL='0'), dict(NIUMA_MAIL_BUDGET_DAILY='nan')):
            with self.subTest(changes=changes), patch.dict(os.environ, dict(self.values, **changes), clear=True):
                self.assertFalse(mail.configured())
                with patch.object(mail.smtplib, 'SMTP_SSL') as smtp, self.assertRaises(mail.DeliveryError):
                    mail.send_verification('recipient@example.test', '123456', 'register')
                smtp.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_rolling_window_total_and_restart_never_refund_attempts(self):
        with patch.object(budget.time, 'time', return_value=100000):
            self.reserve()
            self.reserve()
            with self.assertRaises(budget.BudgetError):
                self.reserve()
        with patch.object(budget.time, 'time', return_value=186401):
            self.reserve()
        with patch.object(budget.time, 'time', return_value=272802), self.assertRaises(budget.BudgetError):
            self.reserve()
        self.assertEqual(self.count(), 3)
        if os.name == 'posix':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_config_increases_do_not_raise_the_original_persisted_ceiling(self):
        self.reserve(NIUMA_MAIL_BUDGET_DAILY='1', NIUMA_MAIL_BUDGET_TOTAL='1')
        with self.assertRaises(budget.BudgetError):
            self.reserve(NIUMA_MAIL_BUDGET_DAILY='200', NIUMA_MAIL_BUDGET_TOTAL='2000')
        self.assertEqual(self.count(), 1)

    def test_exhaustion_and_unavailable_storage_prevent_smtp_connection(self):
        self.reserve(NIUMA_MAIL_BUDGET_TOTAL='1')
        for path in (self.path, self.path.parent / 'missing' / 'quota.db'):
            with patch.dict(os.environ, dict(self.values, NIUMA_MAIL_BUDGET_FILE=str(path)), clear=True), \
                    patch.object(mail.smtplib, 'SMTP_SSL') as smtp, self.assertRaises(mail.DeliveryError):
                mail.send_verification('recipient@example.test', '123456', 'register')
            smtp.assert_not_called()

    def test_corrupted_counter_is_not_replaced_by_a_fresh_budget(self):
        self.path.write_bytes(b'not a sqlite database')
        with self.assertRaises(budget.BudgetError):
            self.reserve()
        self.assertEqual(self.path.read_bytes(), b'not a sqlite database')

    def test_smtp_failure_consumes_budget_for_every_verification_purpose(self):
        values = dict(self.values, NIUMA_MAIL_BUDGET_DAILY='4', NIUMA_MAIL_BUDGET_TOTAL='4')
        with patch.dict(os.environ, values, clear=True), patch.object(mail.smtplib, 'SMTP_SSL') as smtp:
            smtp.side_effect = smtplib.SMTPException('Synthetic provider failure')
            for purpose in ('register', 'verify_email', 'reset_password', 'register'):
                with self.assertRaises(mail.DeliveryError):
                    mail.send_verification('recipient@example.test', '123456', purpose)
            self.assertEqual(smtp.call_count, 4)
            with self.assertRaises(mail.DeliveryError):
                mail.send_verification('recipient@example.test', '123456', 'register')
            self.assertEqual(smtp.call_count, 4)
        self.assertEqual(self.count(), 4)
        data = self.path.read_bytes()
        for private in (b'recipient', b'123456', b'Synthetic-private-mail-password'):
            self.assertNotIn(private, data)

    def test_test_and_production_processes_share_one_atomic_counter(self):
        script = '''import os,mail_budget
try:
    mail_budget.reserve(mail_budget.configuration(os.environ))
except mail_budget.BudgetError:
    raise SystemExit(3)
'''
        def invoke(number):
            env = dict(os.environ, **self.values, NIUMA_ENVIRONMENT='test' if number % 2 else 'production',
                       PYTHONPATH=str(Path(__file__).resolve().parent))
            return subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, timeout=15).returncode
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            statuses = list(executor.map(invoke, range(8)))
        self.assertEqual(statuses.count(0), 2, statuses)
        self.assertEqual(statuses.count(3), 6, statuses)
        self.assertEqual(self.count(), 2)


if __name__ == '__main__':
    unittest.main()
