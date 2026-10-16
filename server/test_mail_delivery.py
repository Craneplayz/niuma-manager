import io
import os
import smtplib
import ssl
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import mail_delivery as mail


class MailDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.settings = {'NIUMA_MAIL_FROM': 'no-reply@mail.example.com',
                         'NIUMA_MAIL_PASSWORD': 'synthetic$UNRELATED ${HOME} # \\literal'}
        self.environment = patch.dict(os.environ, self.settings, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_tls_sender_identity_and_one_recipient(self):
        for purpose in ('register', 'verify_email', 'verify'):
            with self.subTest(purpose=purpose), patch.object(mail.smtplib, 'SMTP_SSL') as connect:
                connect.return_value.__enter__.return_value.send_message.return_value = {}
                mail.send_verification('student@example.net', '031009', purpose)
                args, options = connect.call_args
                self.assertEqual(args, ('smtpdm.aliyun.com', 465))
                self.assertEqual(options['timeout'], 10)
                self.assertEqual(options['context'].verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(options['context'].check_hostname)
                client = connect.return_value.__enter__.return_value
                client.login.assert_called_once_with(self.settings['NIUMA_MAIL_FROM'], self.settings['NIUMA_MAIL_PASSWORD'])
                message = client.send_message.call_args.args[0]
                self.assertIn(self.settings['NIUMA_MAIL_FROM'], str(message['From']))
                self.assertEqual(message['To'], 'student@example.net')
                self.assertEqual(message['Subject'], '牛马管理器 · ' + mail.PURPOSES[purpose] + '验证码')
                self.assertIn('031009', message.get_content())
                self.assertIn('10 分钟内有效', message.get_content())
                self.assertNotIn(self.settings['NIUMA_MAIL_PASSWORD'], message.as_string())
                self.assertEqual(client.send_message.call_args.kwargs,
                                 {'from_addr': self.settings['NIUMA_MAIL_FROM'], 'to_addrs': ['student@example.net']})
                client.set_debuglevel.assert_not_called()

    def test_official_region_hosts_only(self):
        for host in mail.SMTP_HOSTS:
            with self.subTest(host=host), patch.dict(os.environ, NIUMA_MAIL_HOST=host), \
                    patch.object(mail.smtplib, 'SMTP_SSL') as connect:
                connect.return_value.__enter__.return_value.send_message.return_value = {}
                mail.send_verification('student@example.net', '123456', 'register')
                self.assertEqual(connect.call_args.args[0], host)
        for host in ('localhost', '127.0.0.1', 'smtpdm.aliyun.com.evil.test',
                     'smtpdm-ap-southeast-1.aliyun.com', 'https://smtpdm.aliyun.com', 'smtpdm.aliyun.com:465'):
            with self.subTest(host=host), patch.dict(os.environ, NIUMA_MAIL_HOST=host), \
                    patch.object(mail.smtplib, 'SMTP_SSL') as connect:
                self.assertFalse(mail.configured())
                with self.assertRaises(mail.DeliveryError):
                    mail.send_verification('student@example.net', '123456', 'register')
                connect.assert_not_called()

    def test_normalized_international_domain_is_deliverable(self):
        email = 'student@xn--fsqu00a.xn--0zwm56d'
        with patch.object(mail.smtplib, 'SMTP_SSL') as connect:
            connect.return_value.__enter__.return_value.send_message.return_value = {}
            mail.send_verification(email, '123456', 'register')
            self.assertEqual(connect.return_value.__enter__.return_value.send_message.call_args.kwargs['to_addrs'], [email])

    def test_unconfigured_service_fails_closed_without_network(self):
        for values in ({}, {'NIUMA_MAIL_FROM': 'no-reply@example.com'}, {'NIUMA_MAIL_PASSWORD': 'synthetic'}):
            with self.subTest(values=bool(values)), patch.dict(os.environ, values, clear=True), \
                    patch.object(mail.smtplib, 'SMTP_SSL') as connect:
                self.assertFalse(mail.configured())
                with self.assertRaises(mail.DeliveryError) as failure:
                    mail.send_verification('student@example.net', '123456', 'register')
                self.assertEqual(failure.exception.message, mail.NOT_CONFIGURED)
                self.assertEqual(failure.exception.code, 'email_not_configured')
                connect.assert_not_called()

    def test_invalid_settings_never_connect(self):
        for key, value in [('NIUMA_MAIL_PORT', '25'), ('NIUMA_MAIL_PORT', '80'), ('NIUMA_MAIL_PORT', '587'),
                           ('NIUMA_MAIL_PORT', 'invalid'), ('NIUMA_MAIL_TIMEOUT', '0'),
                           ('NIUMA_MAIL_TIMEOUT', '31'), ('NIUMA_MAIL_TIMEOUT', 'nan'),
                           ('NIUMA_MAIL_FROM', 'sender@example.com\r\nBcc: victim@example.org'),
                           ('NIUMA_MAIL_FROM', 'Display Name <sender@example.com>'),
                           ('NIUMA_MAIL_PASSWORD', 'synthetic\npassword')]:
            with self.subTest(key=key), patch.dict(os.environ, {key: value}), \
                    patch.object(mail.smtplib, 'SMTP_SSL') as connect:
                self.assertFalse(mail.configured())
                with self.assertRaises(mail.DeliveryError):
                    mail.send_verification('student@example.net', '123456', 'register')
                connect.assert_not_called()

    def test_input_cannot_inject_headers_or_expand_recipient_list(self):
        for email, code, purpose in [('a@example.org,b@example.org', '123456', 'register'),
                                     ('student@example.org\r\nBcc: a@example.org', '123456', 'register'),
                                     ('student@example.org', '123456\n', 'register'),
                                     ('student@example.org', '１２３４５６', 'register'),
                                     ('student@example.org', '12345', 'register'),
                                     ('student@example.org', '123456', 'password_reset'),
                                     ('student@example.org', '123456', ['register'])]:
            with self.subTest(purpose=str(purpose)), patch.object(mail.smtplib, 'SMTP_SSL') as connect:
                with self.assertRaises(mail.DeliveryError):
                    mail.send_verification(email, code, purpose)
                connect.assert_not_called()

    def test_provider_and_network_failures_do_not_log_or_expose_sensitive_values(self):
        secret_response = self.settings['NIUMA_MAIL_PASSWORD'] + ' student@example.net 123456'
        failures = [smtplib.SMTPAuthenticationError(535, secret_response.encode()),
                    smtplib.SMTPRecipientsRefused({'student@example.net': (550, secret_response)}),
                    smtplib.SMTPDataError(554, secret_response.encode()),
                    smtplib.SMTPServerDisconnected(secret_response),
                    ssl.SSLCertVerificationError(secret_response), TimeoutError(secret_response)]
        for error in failures:
            for phase in ('connect', 'login', 'send'):
                with self.subTest(error=type(error).__name__, phase=phase), \
                        patch.object(mail.smtplib, 'SMTP_SSL') as connect:
                    client = connect.return_value.__enter__.return_value
                    if phase == 'connect':
                        connect.side_effect = error
                    elif phase == 'login':
                        client.login.side_effect = error
                    else:
                        client.send_message.side_effect = error
                    output = io.StringIO()
                    with redirect_stdout(output), redirect_stderr(output), self.assertRaises(mail.DeliveryError) as failure:
                        mail.send_verification('student@example.net', '123456', 'register')
                    self.assertEqual(failure.exception.message, mail.UNAVAILABLE)
                    self.assertEqual(failure.exception.code, 'email_send_failed')
                    self.assertEqual(str(failure.exception), mail.UNAVAILABLE)
                    self.assertTrue(failure.exception.__suppress_context__)
                    self.assertEqual(output.getvalue(), '')
                    self.assertEqual(connect.call_count, 1)

    def test_recipient_refusal_result_is_failure(self):
        with patch.object(mail.smtplib, 'SMTP_SSL') as connect:
            connect.return_value.__enter__.return_value.send_message.return_value = {
                'student@example.net': (550, b'sensitive provider reply')}
            with self.assertRaises(mail.DeliveryError) as failure:
                mail.send_verification('student@example.net', '123456', 'register')
            self.assertEqual(failure.exception.code, 'email_send_failed')
            self.assertEqual(failure.exception.message, mail.UNAVAILABLE)

    def test_configured_is_read_only(self):
        with patch.object(mail.smtplib, 'SMTP_SSL') as connect:
            self.assertTrue(mail.configured(self.settings))
            self.assertFalse(mail.configured({}))
            connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
