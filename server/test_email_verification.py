import concurrent.futures
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from jsonschema import Draft202012Validator

import app
import email_verification as verification
from auth_test_support import LEGACY_EMAIL_DOMAINS, seed_legacy_account

CONTRACT = json.loads((Path(__file__).resolve().parents[1] / 'contracts/schemas/email-auth-v0.1.schema.json').read_text())


def check_response(name, value):
    Draft202012Validator({'$schema': CONTRACT['$schema'], '$defs': CONTRACT['$defs'],
                          '$ref': '#/$defs/' + name}).validate(value)


class EmailVerificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'accounts.sqlite3'
        self.environment = patch.dict(app.os.environ, NIUMA_ENVIRONMENT='test', NIUMA_ENABLE_WECHAT='0',
                                      NIUMA_COOKIE_SECURE='0', NIUMA_TRUST_PROXY_CLIENT_IP='0')
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.database = patch.object(app, 'DB', self.path)
        self.database.start()
        self.addCleanup(self.database.stop)
        app.initialize(recover=False)
        self.mailbox = []
        self.configured = patch.object(verification.mail_delivery, 'configured', return_value=True)
        self.configured.start()
        self.addCleanup(self.configured.stop)
        self.delivery = patch.object(verification.mail_delivery, 'send_verification',
                                     side_effect=lambda *message: self.mailbox.append(message))
        self.delivery.start()
        self.addCleanup(self.delivery.stop)
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.client = self.new_client()

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def new_client(self):
        return build_opener(HTTPCookieProcessor(CookieJar()))

    def call(self, route, body=None, client=None, headers=None):
        request = Request(self.base + '/api' + route,
                          data=None if body is None else json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            response = (client or self.client).open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            value = json.load(response)
            if response.status >= 400:
                check_response('errorResponse', value)
            elif route == '/auth/email-code':
                check_response('challengeResponse', value)
            elif route in ('/auth/register', '/auth/verify-email'):
                check_response('verifiedUserResponse', value)
            elif route in ('/auth/login', '/auth/me'):
                check_response('userResponse', value)
            return response.status, value, response.headers

    def challenge(self, email='new@example.test', purpose='register', client=None):
        status, result, _ = self.call('/auth/email-code', dict(email=email, purpose=purpose), client)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['expiresIn'], 600)
        self.assertEqual(result['retryAfter'], 60)
        recipient, code, actual_purpose = self.mailbox[-1]
        self.assertEqual(recipient, email.lower())
        self.assertEqual(actual_purpose, purpose)
        return result['challengeId'], code

    def register(self, email='new@example.test', challenge_id=None, code=None, client=None):
        return self.call('/auth/register', dict(email=email, name='Synthetic', password='Synthetic-2026!',
                                               challengeId=challenge_id, code=code), client)

    def legacy(self, email='old@example.test', client=None):
        seed_legacy_account(self.path, dict(email=email, name='Original', password='Legacy-2026!'))
        status, result, _ = self.call('/auth/login', dict(email=email, password='Legacy-2026!'), client)
        self.assertEqual(status, 200)
        self.assertFalse(result['user']['emailVerified'])
        return result['user']['id']

    def test_new_registration_requires_delivery_and_marks_email_verified(self):
        self.assertEqual(self.register()[0], 400)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
        challenge_id, code = self.challenge('NEW@example.test')
        with app.connection() as db:
            stored = db.execute('SELECT code_hash,status FROM email_challenges').fetchone()
            self.assertNotEqual(stored['code_hash'], code)
            self.assertNotEqual(stored['code_hash'], hashlib.sha256(code.encode()).hexdigest())
            self.assertEqual(stored['status'], 'active')
        status, account, headers = self.register(challenge_id=challenge_id, code=code)
        self.assertEqual(status, 200, account)
        self.assertTrue(account['user']['emailVerified'])
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertTrue(self.call('/auth/me')[1]['user']['emailVerified'])
        self.assertEqual(self.call('/workspace')[0], 200)
        self.assertEqual(self.register(challenge_id=challenge_id, code=code)[0], 409)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT status FROM email_challenges').fetchone()[0], 'consumed')

    def test_missing_configuration_blocks_new_registration_but_not_legacy_login(self):
        self.legacy()
        with patch.object(verification.mail_delivery, 'configured', return_value=False):
            status, result, _ = self.call('/auth/email-code', dict(email='new@example.test', purpose='register'))
        self.assertEqual(status, 503)
        self.assertEqual(result['code'], 'email_not_configured')
        self.assertEqual(self.call('/workspace')[0], 200)
        self.assertEqual(self.register()[0], 400)
        self.assertEqual(self.mailbox, [])

    def test_legacy_unicode_domain_rejects_registration_codes_for_equivalent_addresses(self):
        email = 'student@例子.中国'
        uid = self.legacy(email)
        for address in (email, 'student@xn--fsqu00a.xn--fiqs8s', ' Student@例子.中国 '):
            with self.subTest(address=address):
                status, result, headers = self.call('/auth/email-code',
                                                   dict(email=address, purpose='register'), self.new_client())
                self.assertEqual(status, 409, result)
                self.assertEqual(result['code'], 'email_registered')
                self.assertIsNone(headers.get('Set-Cookie'))
        self.assertEqual(self.mailbox, [])
        with app.connection() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT id,email FROM users')], [(uid, email)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM email_challenges').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM email_rate_events').fetchone()[0], 0)

    def test_legacy_unicode_domain_rejects_registration_with_an_existing_valid_code(self):
        email = 'student@例子.中国'
        canonical = 'student@xn--fsqu00a.xn--fiqs8s'
        # An outstanding code must not bypass the duplicate check when an
        # equivalent legacy account appears before the registration transaction.
        challenge_id, code = self.challenge(canonical)
        uid = self.legacy(email)
        task = dict(id='original-task', title='Existing task', status='not_started', due=None, plans=[], subtasks=[])
        with app.connection() as db:
            original = dict(db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
            db.execute('INSERT INTO workspace VALUES(?,?,?,?)', (uid, 'tasks', json.dumps([task]), 7))
        for address in (canonical, email, ' Student@例子.中国 '):
            with self.subTest(address=address):
                status, result, headers = self.register(address, challenge_id, code, self.new_client())
                self.assertEqual(status, 409, result)
                self.assertIsNone(headers.get('Set-Cookie'))
        with app.connection() as db:
            self.assertEqual([dict(row) for row in db.execute('SELECT * FROM users')], [original])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM verified_emails').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?',
                                        (challenge_id,)).fetchone()[0], 'active')
        status, result, _ = self.call('/auth/login', dict(email=email, password='Legacy-2026!'))
        self.assertEqual(status, 200, result)
        self.assertEqual(result['user']['id'], uid)
        self.assertEqual(result['user']['email'], email)
        self.assertFalse(result['user']['emailVerified'])
        self.assertEqual(self.call('/workspace')[1]['tasks'], dict(value=[task], version=7))

    def test_normalized_legacy_domains_block_registration_without_changing_accounts(self):
        for index, (stored_domain, canonical_domain) in enumerate(LEGACY_EMAIL_DOMAINS):
            email = f'student-{index}@{stored_domain}'
            canonical = f'student-{index}@{canonical_domain}'
            with self.subTest(email=email):
                self.assertEqual(verification.normalize_email(email), canonical)
                challenge_id, code = self.challenge(canonical)
                uid = self.legacy(email)
                tasks = [dict(id=f'original-{index}', title='Existing task', status='not_started',
                              due=None, plans=[], subtasks=[])]
                with app.connection() as db:
                    if index % 2:
                        verification.mark_verified(db, uid)
                    db.execute('INSERT INTO workspace VALUES(?,?,?,?)', (uid, 'tasks', json.dumps(tasks), 7))
                    before = '\n'.join(db.iterdump())
                delivered = len(self.mailbox)
                for address in (canonical, email):
                    status, result, _ = self.call('/auth/email-code',
                                                  dict(email=address, purpose='register'), self.new_client())
                    self.assertEqual(status, 409, result)
                    self.assertEqual(result['code'], 'email_registered')
                    status, result, headers = self.register(address, challenge_id, code, self.new_client())
                    self.assertEqual(status, 409, result)
                    self.assertIsNone(headers.get('Set-Cookie'))
                self.assertEqual(len(self.mailbox), delivered)
                with app.connection() as db:
                    self.assertEqual('\n'.join(db.iterdump()), before)
                status, result, _ = self.call('/auth/login', dict(email=email, password='Legacy-2026!'))
                self.assertEqual(status, 200, result)
                self.assertEqual(result['user']['id'], uid)
                self.assertEqual(result['user']['email'], email)
                self.assertEqual(result['user']['emailVerified'], bool(index % 2))
                self.assertEqual(self.call('/workspace')[1]['tasks'], dict(value=tasks, version=7))

    def test_account_lookup_skips_invalid_legacy_addresses_and_preserves_existing_collisions(self):
        for email in ('invalid@@example.test', 'user@localhost', '坏本地名@example.test'):
            seed_legacy_account(self.path, dict(email=email, name='Original', password='Legacy-2026!'))
        unicode_email, canonical = 'student@ｅｘａｍｐｌｅ.test', 'student@example.test'
        uid = self.legacy(unicode_email)
        with app.connection() as db:
            before = '\n'.join(db.iterdump())
            self.assertEqual(verification.account_for_email(db, canonical)['id'], uid)
            self.assertIsNone(verification.account_for_email(db, 'missing@example.test'))
            self.assertIsNone(verification.account_for_email(db, 'user@xn--a.test'))
            self.assertEqual('\n'.join(db.iterdump()), before)
        # Already conflicting historical identities are never rewritten or merged.
        canonical_uid = self.legacy(canonical, self.new_client())
        with app.connection() as db:
            before = '\n'.join(db.iterdump())
            self.assertEqual(verification.account_for_email(db, unicode_email)['id'], uid)
            self.assertEqual(verification.account_for_email(db, canonical)['id'], canonical_uid)
            self.assertEqual('\n'.join(db.iterdump()), before)

    def test_old_account_verifies_its_own_email_without_losing_data(self):
        uid = self.legacy()
        task = dict(id='original-task', title='Existing task', status='not_started', due=None, plans=[], subtasks=[])
        with app.connection() as db:
            db.execute('INSERT INTO workspace VALUES(?,?,?,?)', (uid, 'tasks', json.dumps([task]), 7))
        challenge_id, code = self.challenge('old@example.test', 'verify_email')
        status, result, _ = self.call('/auth/verify-email', dict(challengeId=challenge_id, code=code, email='attacker@example.test'))
        self.assertEqual(status, 200, result)
        self.assertTrue(result['user']['emailVerified'])
        self.assertEqual(result['user']['id'], uid)
        stored = self.call('/workspace')[1]['tasks']
        self.assertEqual(stored, dict(value=[task], version=7))
        self.assertEqual(self.call('/auth/email-code', dict(purpose='verify_email'))[0], 409)
        self.assertEqual(self.call('/auth/verify-email', dict(challengeId=challenge_id, code=code))[0], 409)

    def test_failed_guesses_persist_and_five_failures_disable_a_code(self):
        challenge_id, code = self.challenge()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            status, result, _ = self.register(challenge_id=challenge_id, code=wrong)
            self.assertEqual(status, 400)
            self.assertEqual(result['code'], 'email_code_invalid')
        self.assertEqual(self.register(challenge_id=challenge_id, code=code)[0], 400)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT attempts FROM email_challenges').fetchone()[0], 6)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)

    def test_expired_code_is_rejected_and_sending_again_invalidates_previous_code(self):
        with patch.object(verification.time, 'time', return_value=100000):
            first, first_code = self.challenge()
        with patch.object(verification.time, 'time', return_value=100601):
            status, result, _ = self.register(challenge_id=first, code=first_code)
            self.assertEqual(status, 400)
            self.assertEqual(result['code'], 'email_code_expired')
            second, second_code = self.challenge()
            self.assertEqual(self.register(challenge_id=first, code=first_code)[0], 400)
            self.assertEqual(self.register(challenge_id=second, code=second_code)[0], 200)

    def test_recent_code_is_superseded_after_resend(self):
        with patch.object(verification.time, 'time', return_value=100000):
            first, first_code = self.challenge()
        with patch.object(verification.time, 'time', return_value=100061):
            second, second_code = self.challenge()
            self.assertEqual(self.register(challenge_id=first, code=first_code)[0], 400)
            self.assertEqual(self.register(challenge_id=second, code=second_code)[0], 200)

    def test_resend_limits_survive_reinitialization_and_have_retry_after(self):
        self.challenge()
        app.initialize(recover=False)
        status, result, headers = self.call('/auth/email-code', dict(email='new@example.test', purpose='register'))
        self.assertEqual(status, 429)
        self.assertGreater(result['retryAfter'], 0)
        self.assertEqual(headers['Retry-After'], str(result['retryAfter']))
        self.assertEqual(len(self.mailbox), 1)

    def test_email_ip_and_global_quotas_apply(self):
        for scope in ('email', 'ip', 'global'):
            with self.subTest(scope=scope), patch.object(verification, 'SEND_LIMITS', ((scope, 3600, 1),)):
                with app.connection() as db:
                    db.execute('DELETE FROM email_rate_events')
                self.challenge()
                email = 'new@example.test' if scope == 'email' else 'different@example.test'
                self.assertEqual(self.call('/auth/email-code', dict(email=email, purpose='register'))[0], 429)

    def test_guess_quota_is_not_reset_by_resending(self):
        with patch.object(verification, 'GUESS_LIMITS', (('email', 3600, 2),)):
            with patch.object(verification.time, 'time', return_value=100000):
                challenge_id, code = self.challenge()
                self.assertEqual(self.register(challenge_id=challenge_id, code='bad')[0], 400)
                self.assertEqual(self.register(challenge_id=challenge_id, code='bad')[0], 400)
            with patch.object(verification.time, 'time', return_value=100061):
                challenge_id, code = self.challenge()
                self.assertEqual(self.register(challenge_id=challenge_id, code=code)[0], 429)

    def test_unknown_challenges_cannot_exhaust_another_emails_guess_budget(self):
        challenge_id, code = self.challenge()
        with patch.object(verification, 'GUESS_LIMITS', (('email', 3600, 2), ('ip', 3600, 100))):
            for _ in range(3):
                self.assertEqual(self.register(challenge_id='unknown-id', code='000000')[0], 400)
            self.assertEqual(self.register(challenge_id=challenge_id, code=code)[0], 200)

    def test_send_failure_invalidates_code_and_does_not_leak_provider_error(self):
        with patch.object(verification.mail_delivery, 'send_verification',
                          side_effect=verification.mail_delivery.DeliveryError()):
            status, result, _ = self.call('/auth/email-code', dict(email='new@example.test', purpose='register'))
        self.assertEqual(status, 503)
        self.assertEqual(result['code'], 'email_send_failed')
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT status FROM email_challenges').fetchone()[0], 'failed')
        self.assertEqual(self.call('/auth/email-code', dict(email='new@example.test', purpose='register'))[0], 429)

    def test_codes_are_bound_to_email_purpose_and_logged_in_owner(self):
        self.assertEqual(self.call('/auth/email-code', dict(purpose='verify_email'))[0], 401)
        register_id, register_code = self.challenge()
        self.assertEqual(self.register('different@example.test', register_id, register_code)[0], 400)
        first_user = self.legacy()
        verify_id, verify_code = self.challenge('old@example.test', 'verify_email')
        self.assertEqual(self.register('old@example.test', verify_id, verify_code)[0], 409)
        other = self.new_client()
        self.legacy('other@example.test', other)
        self.assertEqual(self.call('/auth/verify-email', dict(challengeId=verify_id, code=verify_code), other)[0], 400)
        self.assertEqual(self.call('/auth/verify-email', dict(challengeId=register_id, code=register_code))[0], 400)
        self.assertEqual(self.call('/auth/verify-email', dict(challengeId=verify_id, code=verify_code))[0], 200)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT user_id FROM verified_emails').fetchone()[0], first_user)

    def test_simultaneous_registration_creates_only_one_user(self):
        challenge_id, code = self.challenge()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.register(challenge_id=challenge_id, code=code, client=self.new_client()), range(2)))
        self.assertEqual(sorted(item[0] for item in results), [200, 409])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)

    def test_consumption_rolls_back_when_account_creation_fails(self):
        challenge_id, code = self.challenge()
        with patch.object(app, 'password_hash', side_effect=RuntimeError('synthetic failure')):
            self.assertEqual(self.register(challenge_id=challenge_id, code=code)[0], 502)
        self.assertEqual(self.register(challenge_id=challenge_id, code=code)[0], 200)

    def test_untrusted_client_cannot_forge_rate_limit_identity(self):
        self.challenge()
        headers = {'X-Niuma-Client-IP': '198.51.100.2', 'X-Forwarded-For': '198.51.100.3'}
        with patch.object(verification, 'SEND_LIMITS', (('ip', 3600, 1),)):
            self.assertEqual(self.call('/auth/email-code', dict(email='other@example.test', purpose='register'), headers=headers)[0], 429)
        with app.connection() as db:
            self.assertNotIn('127.0.0.1', ''.join(row[0] for row in db.execute('SELECT subject FROM email_rate_events')))

    def test_invalid_addresses_and_unknown_purposes_never_send(self):
        for address in ('no-at-sign', 'a@@example.test', 'a\r\nBcc: b@example.test', 'a..b@example.test', 'a@-example.test'):
            self.assertEqual(self.call('/auth/email-code', dict(email=address, purpose='register'))[0], 400)
        self.assertEqual(self.call('/auth/email-code', dict(email='new@example.test', purpose='change_email'))[0], 400)
        self.assertEqual(self.mailbox, [])

    def test_challenge_cannot_be_used_in_another_environment(self):
        challenge_id, code = self.challenge()
        other_path = Path(self.directory.name) / 'another' / 'accounts.sqlite3'
        with patch.object(app, 'DB', other_path):
            app.initialize(recover=False)
            self.assertEqual(self.register(challenge_id=challenge_id, code=code)[0], 400)

    def test_internationalized_domain_can_be_used_to_log_in_after_registration(self):
        email = 'student@例子.中国'
        status, challenge, _ = self.call('/auth/email-code', dict(email=email, purpose='register'))
        self.assertEqual(status, 200, challenge)
        code = self.mailbox[-1][1]
        status, account, _ = self.register(email, challenge['challengeId'], code)
        self.assertEqual(status, 200, account)
        self.assertEqual(account['user']['email'], 'student@xn--fsqu00a.xn--fiqs8s')
        status, account, _ = self.call('/auth/login', dict(email=email, password='Synthetic-2026!'), self.new_client())
        self.assertEqual(status, 200, account)
        self.assertTrue(account['user']['emailVerified'])


if __name__ == '__main__':
    unittest.main()
