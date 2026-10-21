"""Password recovery through real HTTP; delivery only uses a synthetic mailbox."""
import concurrent.futures
import json
import sqlite3
import threading
import unittest
from unittest.mock import patch

import app
import email_verification as verification
import test_email_verification as email_tests
from auth_test_support import LEGACY_EMAIL_DOMAINS


OLD_PASSWORD = 'Legacy-2026!'
NEW_PASSWORD = 'Recovered-2026!'


class PasswordRecoveryTests(email_tests.EmailVerificationTests):
    def call(self, route, body=None, client=None, headers=None):
        result = super().call(route, body, client, headers)
        if route == '/auth/reset-password' and result[0] == 200:
            email_tests.check_response('resetPasswordResponse', result[1])
        return result

    def verified_account(self, email='recovery@example.test', client=None):
        uid = self.legacy(email, client)
        with app.connection() as db:
            verification.mark_verified(db, uid)
        return uid

    def recovery(self, email='recovery@example.test', client=None):
        previous = len(self.mailbox)
        status, result, _ = self.call('/auth/email-code',
                                      dict(email=email, purpose='reset_password'), client)
        self.assertEqual(status, 200, result)
        self.assertEqual(len(self.mailbox), previous + 1)
        recipient, code, purpose = self.mailbox[-1]
        self.assertEqual(recipient, verification.normalize_email(email))
        self.assertEqual(purpose, 'reset_password')
        return result['challengeId'], code

    def reset(self, challenge_id, code, email='recovery@example.test',
              password=NEW_PASSWORD, client=None):
        return self.call('/auth/reset-password',
                         dict(email=email, challengeId=challenge_id, code=code,
                              newPassword=password), client)

    def login(self, email='recovery@example.test', password=OLD_PASSWORD, client=None):
        return self.call('/auth/login', dict(email=email, password=password),
                         client or self.new_client())

    def legacy_delivery_challenge(self, email, verify=True):
        client = self.new_client()
        uid = self.legacy(email, client)
        def old_normalize(value):
            local, domain = value.strip().lower().split('@')
            return local + '@' + domain.encode('idna').decode('ascii')
        with patch.object(verification, 'normalize_email', side_effect=old_normalize):
            with app.connection() as db:
                db.execute('DELETE FROM email_rate_events')
            status, challenge, _ = self.call('/auth/email-code', dict(purpose='verify_email'), client)
            self.assertEqual(status, 200, challenge)
            code = self.mailbox[-1][1]
            if verify:
                self.assertEqual(self.call('/auth/verify-email', dict(challengeId=challenge['challengeId'], code=code), client)[0], 200)
                with app.connection() as db:
                    db.execute('DELETE FROM email_rate_events')
                challenge_id, code = self.recovery(email)
            else:
                challenge_id = challenge['challengeId']
        return uid, client, challenge_id, code, self.mailbox[-1][0]

    def test_recovery_upgrade_revokes_only_provably_changed_unicode_identities_once(self):
        changed = ('old@faß.test', 'sigma@ος.test')
        preserved = ('old@fass.test', 'old@xn--fa-hia.test', 'full@ｅｘａｍｐｌｅ.test',
                     'chinese@例子.中国', 'dot@example。test', 'dot@example．test',
                     'dot@example｡test', 'nfc@bu\u0308cher.test')
        fixtures = {email: self.legacy_delivery_challenge(email) for email in (*changed, *preserved)}
        self.assertEqual(fixtures[changed[0]][4], 'old@fass.test')
        self.assertEqual(fixtures[changed[1]][4], 'sigma@xn--0xai.test')
        with app.connection() as db:
            for email, (uid, _, _, _, _) in fixtures.items():
                db.execute('INSERT INTO workspace VALUES(?,?,?,?)', (uid, 'tasks', '[{"id":"preserve"}]', 7))
                db.execute('INSERT INTO sessions VALUES(?,?,?)', ('synthetic-admin-' + uid, uid, 9999999999))
            users_before = [tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')]
            workspace_before = [tuple(row) for row in db.execute('SELECT * FROM workspace ORDER BY user_id')]
            sessions_before = {uid: [tuple(row) for row in db.execute('SELECT * FROM sessions WHERE user_id=? ORDER BY token', (uid,))]
                               for uid, *_ in fixtures.values()}
            # This table did not exist in bb33179; all other rows are old state.
            db.execute('DROP TABLE email_identity_migrations')
        app.initialize(recover=False)
        with app.connection() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')], users_before)
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM workspace ORDER BY user_id')], workspace_before)
            for email, (uid, _, challenge_id, _, _) in fixtures.items():
                self.assertEqual(verification.verified(db, uid), email in preserved)
                self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?', (challenge_id,)).fetchone()[0],
                                 'active' if email in preserved else 'superseded')
                sessions = [tuple(row) for row in db.execute('SELECT * FROM sessions WHERE user_id=? ORDER BY token', (uid,))]
                self.assertEqual(sessions, sessions_before[uid] if email in preserved else [])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM email_identity_migrations').fetchone()[0], 1)
        for email in changed:
            uid, client, challenge_id, code, wrong_recipient = fixtures[email]
            self.assertEqual(self.call('/auth/me', client=client)[0], 401)
            for address in (email, wrong_recipient, verification.normalize_email(email)):
                self.assertEqual(self.reset(challenge_id, code, email=address)[0], 400)
            self.assertEqual(self.login(email)[1]['user']['id'], uid)
        # A legitimate old ASCII challenge still works and targets only its owner.
        uid, client, challenge_id, code, _ = fixtures['old@fass.test']
        self.assertEqual(self.call('/auth/me', client=client)[0], 200)
        self.assertEqual(self.reset(challenge_id, code, email='old@fass.test')[0], 200)
        self.assertEqual(self.login('old@fass.test', NEW_PASSWORD)[1]['user']['id'], uid)
        # Reverification to the correct mailbox survives subsequent restarts.
        uid, *_ = fixtures[changed[0]]
        self.assertEqual(self.login(changed[0], client=self.client)[0], 200)
        with app.connection() as db:
            db.execute('DELETE FROM email_rate_events')
        status, challenge, _ = self.call('/auth/email-code', dict(purpose='verify_email'))
        self.assertEqual(status, 200, challenge)
        self.assertEqual(self.mailbox[-1][0], 'old@xn--fa-hia.test')
        self.assertEqual(self.call('/auth/verify-email', dict(challengeId=challenge['challengeId'], code=self.mailbox[-1][1]))[0], 200)
        with app.connection() as db:
            db.execute('DELETE FROM email_rate_events')
        challenge_id, code = self.recovery(changed[0])
        app.initialize(recover=False)
        self.assertEqual(self.call('/auth/me')[1]['user']['id'], uid)
        self.assertTrue(self.call('/auth/me')[1]['user']['emailVerified'])
        self.assertEqual(self.reset(challenge_id, code, email=changed[0])[0], 200)

    def test_recovery_upgrade_unverified_wrong_target_code_preserves_its_session(self):
        uid, client, challenge_id, code, _ = self.legacy_delivery_challenge('unverified@faß.test', verify=False)
        with app.connection() as db:
            db.execute("UPDATE email_challenges SET status='pending' WHERE id=?", (challenge_id,))
            db.execute('DROP TABLE email_identity_migrations')
        app.initialize(recover=False)
        with app.connection() as db:
            self.assertFalse(verification.verified(db, uid))
            self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?', (challenge_id,)).fetchone()[0], 'superseded')
        self.assertEqual(self.call('/auth/me', client=client)[0], 200)
        self.assertEqual(self.call('/auth/verify-email', dict(challengeId=challenge_id, code=code), client)[0], 400)

    def test_recovery_upgrade_failure_rolls_back_revocation_and_can_retry(self):
        uid, client, challenge_id, _, _ = self.legacy_delivery_challenge('rollback@faß.test')
        with app.connection() as db:
            db.execute('DROP TABLE email_identity_migrations')
            db.execute("CREATE TRIGGER synthetic_upgrade_failure BEFORE DELETE ON sessions "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic upgrade failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            app.initialize(recover=False)
        with app.connection() as db:
            self.assertTrue(verification.verified(db, uid))
            self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?', (challenge_id,)).fetchone()[0], 'active')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM email_identity_migrations').fetchone()[0], 0)
            db.execute('DROP TRIGGER synthetic_upgrade_failure')
        self.assertEqual(self.call('/auth/me', client=client)[0], 200)
        app.initialize(recover=False)
        self.assertEqual(self.call('/auth/me', client=client)[0], 401)
        with app.connection() as db:
            self.assertFalse(verification.verified(db, uid))
            self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?', (challenge_id,)).fetchone()[0], 'superseded')

    def test_recovery_profile_and_password_routes_keep_exact_colliding_identity(self):
        for group, domains in enumerate((('faß.test', 'xn--fa-hia.test', 'fass.test'),
                                         ('ος.test', 'xn--0xag.test', 'οσ.test'))):
            addresses = [f'route-{group}@{domain}' for domain in domains]
            clients = [self.new_client() for _ in addresses]
            ids = [self.verified_account(address, client) for address, client in zip(addresses, clients)]
            challenges = []
            for address in addresses:
                with app.connection() as db:
                    db.execute('DELETE FROM email_rate_events')
                challenges.append(self.recovery(address)[0])
            for selected, uid in enumerate(ids):
                with self.subTest(address=addresses[selected]):
                    with app.connection() as db:
                        other_users = [tuple(row) for row in db.execute('SELECT * FROM users WHERE id!=? ORDER BY id', (uid,))]
                        other_sessions = [tuple(row) for row in db.execute('SELECT * FROM sessions WHERE user_id!=? ORDER BY token', (uid,))]
                        other_codes = [tuple(row) for row in db.execute('SELECT * FROM email_challenges WHERE user_id!=? ORDER BY id', (uid,))]
                    other_email = addresses[(selected + 1) % len(addresses)]
                    status, result, _ = self.call('/auth/profile', dict(name='Updated', email=other_email), clients[selected])
                    self.assertEqual(status, 200, result)
                    self.assertEqual(result['user']['id'], uid)
                    self.assertEqual(result['user']['email'], addresses[selected])
                    new = f'Route-Password-{group}-{selected}!'
                    self.assertEqual(self.call('/auth/password', dict(currentPassword=OLD_PASSWORD, newPassword=new,
                                                                    email=other_email), clients[selected])[0], 200)
                    self.assertEqual(self.call('/auth/me', client=clients[selected])[1]['user']['id'], uid)
                    with app.connection() as db:
                        self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM users WHERE id!=? ORDER BY id', (uid,))], other_users)
                        self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM sessions WHERE user_id!=? ORDER BY token', (uid,))], other_sessions)
                        self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM email_challenges WHERE user_id!=? ORDER BY id', (uid,))], other_codes)
                        self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?', (challenges[selected],)).fetchone()[0], 'superseded')
                    self.assertEqual(self.login(addresses[selected], new)[1]['user']['id'], uid)

    def test_recovery_contextual_sigma_case_and_dot_variants_never_cross_domains(self):
        for index, separator in enumerate(('.', '。', '．', '｡')):
            target=f'sigma-{index}@ος{separator}test'
            uppercase=f'sigma-{index}@ΟΣ{separator}test'
            other=f'sigma-{index}@οσ{separator}test'
            owner=self.new_client()
            uid=self.legacy(target,owner)
            status,challenge,_=self.call('/auth/email-code',dict(purpose='verify_email'),owner)
            self.assertEqual(status,200,challenge)
            self.assertEqual(self.mailbox[-1][0],f'sigma-{index}@xn--0xag.test')
            self.assertEqual(self.call('/auth/verify-email',dict(challengeId=challenge['challengeId'],code=self.mailbox[-1][1]),owner)[0],200)
            count=len(self.mailbox)
            status,unavailable,_=self.call('/auth/email-code',dict(email=uppercase,purpose='reset_password'))
            self.assertEqual(status,200,unavailable)
            self.assertEqual(len(self.mailbox),count)
            self.assertEqual(self.login(uppercase)[0],401)
            with patch.dict(app.os.environ,NIUMA_ADMIN_USER_IDS=uid):
                self.assertEqual(self.call('/admin/auth/login',dict(email=uppercase,password=OLD_PASSWORD),self.new_client())[0],401)
            self.assertEqual(self.reset(unavailable['challengeId'],'000000',email=uppercase)[0],400)
            other_uid=self.verified_account(other,self.new_client())
            self.assertEqual(self.login(uppercase)[1]['user']['id'],other_uid)
            with patch.dict(app.os.environ,NIUMA_ADMIN_USER_IDS=uid):
                self.assertEqual(self.call('/admin/auth/login',dict(email=uppercase,password=OLD_PASSWORD),self.new_client())[0],403)
            with app.connection() as db:
                db.execute('DELETE FROM email_rate_events')
                before=tuple(db.execute('SELECT salt,hash FROM users WHERE id=?',(uid,)).fetchone())
            challenge_id,code=self.recovery(uppercase)
            self.assertEqual(self.mailbox[-1][0],f'sigma-{index}@xn--0xai.test')
            self.assertEqual(self.reset(challenge_id,code,email=target)[0],400)
            self.assertEqual(self.reset(challenge_id,code,email=uppercase)[0],200)
            with app.connection() as db:
                self.assertEqual(tuple(db.execute('SELECT salt,hash FROM users WHERE id=?',(uid,)).fetchone()),before)
                db.execute('DELETE FROM email_rate_events')
            self.assertEqual(self.call('/auth/me',client=owner)[1]['user']['id'],uid)
            self.assertEqual(self.login(target)[1]['user']['id'],uid)
            self.assertEqual(self.login(uppercase,NEW_PASSWORD)[1]['user']['id'],other_uid)

    def test_recovery_rejects_mismatched_delivery_owner_at_issue_and_consumption(self):
        target='binding@ος。test'
        recipient='binding@xn--0xai.test'
        uid=self.verified_account(target)
        with app.connection() as db:
            account=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        count=len(self.mailbox)
        with patch.object(verification,'account_for_email',return_value=account):
            status,challenge,_=self.call('/auth/email-code',dict(email=recipient,purpose='reset_password'))
        self.assertEqual(status,200,challenge)
        self.assertEqual(len(self.mailbox),count)
        # An existing challenge with a valid hash must not authorize a different mailbox.
        with app.connection() as db:
            key=verification._key(app.DB.parent/'email-verification.key')
            challenge_id=challenge['challengeId']
            code='123456'
            db.execute("UPDATE email_challenges SET status='active',code_hash=? WHERE id=?",
                       (verification._hash(key,challenge_id,recipient,'reset_password',uid,code),challenge_id))
            db.execute('INSERT INTO email_reset_credentials VALUES(?,?)',
                       (challenge_id,verification._credential_version(key,account)))
            with self.assertRaises(verification.VerificationError):
                verification.consume_code(db,recipient,'reset_password',uid,challenge_id,code,'127.0.0.1',
                                          app.DB.parent/'email-verification.key',credentials=account)
            self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?',(challenge_id,)).fetchone()[0],'active')

    def test_recovery_changes_password_without_automatically_logging_in(self):
        uid = self.verified_account()
        challenge_id, code = self.recovery(client=self.new_client())
        anonymous = self.new_client()
        status, result, headers = self.reset(challenge_id, code, client=anonymous)
        self.assertEqual(status, 200, result)
        self.assertEqual(result, {'reset': True})
        self.assertIsNone(headers.get('Set-Cookie'))
        self.assertEqual(self.call('/auth/me', client=anonymous)[0], 401)
        self.assertEqual(self.login()[0], 401)
        status, result, _ = self.login(password=NEW_PASSWORD)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['user']['id'], uid)
        self.assertTrue(result['user']['emailVerified'])
        self.assertEqual(self.reset(challenge_id, code)[0], 400)

    def test_recovery_colliding_legacy_addresses_reset_only_the_exact_selected_account(self):
        cases = ((domain, canonical, flags)
                 for domain, canonical in (('例子.中国', 'xn--fsqu00a.xn--fiqs8s'),
                                           ('faß.test', 'xn--fa-hia.test'), ('ος.test', 'xn--0xag.test'))
                 for flags in ((True, True), (True, False), (False, True), (False, False)))
        for index, (domain, canonical, (unicode_verified, idna_verified)) in enumerate(cases):
            addresses=(f'collision{index}@{domain}',f'collision{index}@{canonical}')
            clients=[self.new_client(),self.new_client()]
            ids=[self.legacy(address,client) for address,client in zip(addresses,clients)]
            for uid,is_verified in zip(ids,(unicode_verified,idna_verified)):
                if is_verified:
                    with app.connection() as db:verification.mark_verified(db,uid)
            for selected,eligible in enumerate((unicode_verified,idna_verified)):
                with self.subTest(unicode_verified=unicode_verified,idna_verified=idna_verified,selected=selected):
                    with app.connection() as db:
                        db.execute('DELETE FROM email_rate_events')
                        before={r['id']:(r['salt'],r['hash']) for r in db.execute('SELECT * FROM users')}
                    old_mail=len(self.mailbox)
                    status,result,_=self.call('/auth/email-code',dict(email=addresses[selected],purpose='reset_password'))
                    self.assertEqual(status,200,result)
                    with app.connection() as db:
                        challenge=db.execute('SELECT * FROM email_challenges WHERE id=?',(result['challengeId'],)).fetchone()
                        self.assertEqual(challenge['user_id'],ids[selected] if eligible else '')
                    self.assertEqual(len(self.mailbox),old_mail+int(eligible))
                    if not eligible:
                        self.assertEqual(self.reset(result['challengeId'],'000000',email=addresses[selected])[0],400)
                        continue
                    code=self.mailbox[-1][1]
                    # Equivalent delivery addresses do not permit selecting the other historical account.
                    self.assertEqual(self.reset(result['challengeId'],code,email=addresses[1-selected])[0],400)
                    self.assertEqual(self.reset(result['challengeId'],code,email=addresses[selected])[0],200)
                    self.assertEqual(self.login(addresses[selected],NEW_PASSWORD)[1]['user']['id'],ids[selected])
                    self.assertEqual(self.call('/auth/me',client=clients[selected])[0],401)
                    with app.connection() as db:
                        other=db.execute('SELECT * FROM users WHERE id=?',(ids[1-selected],)).fetchone()
                        self.assertEqual((other['salt'],other['hash']),before[ids[1-selected]])

    def test_recovery_revokes_all_ordinary_and_admin_sessions_but_preserves_data(self):
        uid = self.verified_account()
        second = self.new_client()
        administrator = self.new_client()
        self.assertEqual(self.login(client=second)[0], 200)
        with patch.dict(app.os.environ, NIUMA_ADMIN_USER_IDS=uid):
            self.assertEqual(self.call('/admin/auth/login',
                                      dict(email='recovery@example.test', password=OLD_PASSWORD),
                                      administrator)[0], 200)
            self.assertEqual(self.call('/admin/usage', client=administrator)[0], 200)
        other = self.new_client()
        other_uid = self.legacy('unrelated@example.test', other)
        tasks = [dict(id='existing-task', title='Preserve this task', status='not_started',
                      due=None, plans=[], subtasks=[])]
        with app.connection() as db:
            db.execute('INSERT INTO workspace VALUES(?,?,?,?)', (uid, 'tasks', json.dumps(tasks), 7))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (uid,)).fetchone()[0], 3)
        challenge_id, code = self.recovery()
        # Other outstanding account-bound challenges must be invalidated as part
        # of the same transaction, including a delivery still in progress.
        with app.connection() as db:
            original = db.execute('SELECT * FROM email_challenges WHERE id=?', (challenge_id,)).fetchone()
            for state in ('pending', 'active'):
                db.execute('INSERT INTO email_challenges VALUES(?,?,?,?,?,?,?,?,?)',
                           ('synthetic-' + state, original['email'], 'verify_email', uid,
                            original['code_hash'], original['created_at'], original['expires_at'], 0, state))
        self.assertEqual(self.reset(challenge_id, code)[0], 200)
        for client in (self.client, second):
            self.assertEqual(self.call('/auth/me', client=client)[0], 401)
            self.assertEqual(self.call('/workspace', client=client)[0], 401)
        self.assertEqual(self.call('/admin/usage', client=administrator)[0], 401)
        self.assertEqual(self.call('/auth/me', client=other)[1]['user']['id'], other_uid)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (uid,)).fetchone()[0], 0)
            self.assertEqual(dict(db.execute('SELECT value,version FROM workspace WHERE user_id=?', (uid,)).fetchone()),
                             dict(value=json.dumps(tasks), version=7))
            states = dict(db.execute('SELECT id,status FROM email_challenges'))
            self.assertEqual(states[challenge_id], 'consumed')
            self.assertEqual(states['synthetic-pending'], 'superseded')
            self.assertEqual(states['synthetic-active'], 'superseded')
        self.assertEqual(self.login(password=NEW_PASSWORD, client=self.client)[0], 200)
        self.assertEqual(self.call('/workspace')[1]['tasks'], dict(value=tasks, version=7))

    def test_recovery_unknown_and_unverified_accounts_receive_only_unusable_challenges(self):
        uid = self.legacy('unverified@example.test')
        for email in ('unknown@example.test', 'unverified@example.test'):
            with self.subTest(email=email):
                status, result, headers = self.call('/auth/email-code',
                                                   dict(email=email, purpose='reset_password'),
                                                   self.new_client())
                self.assertEqual(status, 200, result)
                self.assertEqual(set(result), {'challengeId', 'retryAfter', 'expiresIn'})
                self.assertIsNone(headers.get('Set-Cookie'))
                status, error, _ = self.reset(result['challengeId'], '000000', email=email)
                self.assertEqual(status, 400, error)
                self.assertEqual(error['code'], 'email_code_invalid')
        self.assertEqual(self.mailbox, [])
        self.assertEqual(self.call('/auth/me')[1]['user']['id'], uid)
        with app.connection() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT status FROM email_challenges')],
                             ['unavailable', 'unavailable'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM email_rate_events WHERE kind LIKE "send:%"').fetchone()[0], 6)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)

    def test_recovery_ineligible_addresses_still_consume_email_ip_and_global_quotas(self):
        self.verified_account()
        for scope in ('email', 'ip', 'global'):
            with self.subTest(scope=scope), patch.object(verification, 'SEND_LIMITS', ((scope, 3600, 1),)):
                with app.connection() as db:
                    db.execute('DELETE FROM email_rate_events')
                first = dict(email='unknown@example.test', purpose='reset_password')
                self.assertEqual(self.call('/auth/email-code', first)[0], 200)
                second = first if scope == 'email' else dict(email='recovery@example.test', purpose='reset_password')
                status, error, headers = self.call('/auth/email-code', second)
                self.assertEqual(status, 429, error)
                self.assertEqual(error['code'], 'email_code_rate_limited')
                self.assertEqual(headers['Retry-After'], str(error['retryAfter']))
        self.assertEqual(self.mailbox, [])

    def test_recovery_missing_configuration_is_identical_for_all_account_states(self):
        self.verified_account()
        self.legacy('unverified@example.test', self.new_client())
        replies = []
        with patch.object(verification.mail_delivery, 'configured', return_value=False):
            for email in ('recovery@example.test', 'unverified@example.test', 'unknown@example.test'):
                status, result, _ = self.call('/auth/email-code', dict(email=email, purpose='reset_password'))
                self.assertEqual(status, 503, result)
                self.assertEqual(result['code'], 'email_not_configured')
                replies.append(result)
        self.assertEqual(replies, [replies[0]] * 3)
        self.assertEqual(self.mailbox, [])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM email_challenges').fetchone()[0], 0)
        self.assertEqual(self.call('/auth/me')[0], 200)

    def test_recovery_smtp_failure_has_the_same_acknowledgement_as_ineligible_addresses(self):
        self.verified_account()
        self.legacy('unverified@example.test', self.new_client())
        challenges = []
        with patch.object(verification.mail_delivery, 'send_verification',
                          side_effect=verification.mail_delivery.DeliveryError()) as send:
            for email in ('recovery@example.test', 'unverified@example.test', 'unknown@example.test'):
                status, result, _ = self.call('/auth/email-code', dict(email=email, purpose='reset_password'))
                self.assertEqual(status, 200, result)
                self.assertEqual(set(result), {'challengeId', 'retryAfter', 'expiresIn'})
                challenges.append((email, result['challengeId']))
            self.assertEqual(send.call_count, 1)
            delivered_code = send.call_args.args[1]
        self.assertEqual(self.reset(challenges[0][1], delivered_code)[0], 400)
        with app.connection() as db:
            states = dict(db.execute('SELECT email,status FROM email_challenges'))
            self.assertEqual(states, {'recovery@example.test': 'failed',
                                      'unverified@example.test': 'unavailable', 'unknown@example.test': 'unavailable'})
        self.assertEqual(self.login()[0], 200)

    def test_recovery_expired_challenge_cannot_change_the_password(self):
        self.verified_account()
        challenge_id, code = self.recovery()
        with app.connection() as db:
            db.execute('UPDATE email_challenges SET expires_at=0 WHERE id=?', (challenge_id,))
        status, error, _ = self.reset(challenge_id, code)
        self.assertEqual(status, 400, error)
        self.assertEqual(error['code'], 'email_code_invalid')
        self.assertEqual(self.login()[0], 200)
        self.assertEqual(self.login(password=NEW_PASSWORD)[0], 401)

    def test_recovery_expiry_and_wrong_codes_do_not_reveal_account_eligibility(self):
        self.verified_account()
        challenge_id, actual_code = self.recovery()
        status, unavailable, _ = self.call('/auth/email-code',
                                            dict(email='unknown@example.test', purpose='reset_password'))
        self.assertEqual(status, 200)
        wrong = '000000' if actual_code != '000000' else '111111'
        errors = []
        for expired in (False, True):
            if expired:
                with app.connection() as db:
                    db.execute('UPDATE email_challenges SET expires_at=0')
            for email, handle in (('recovery@example.test', challenge_id),
                                   ('unknown@example.test', unavailable['challengeId'])):
                with self.subTest(expired=expired, email=email):
                    status, result, _ = self.reset(handle, wrong, email=email)
                    self.assertEqual(status, 400, result)
                    self.assertEqual(result['code'], 'email_code_invalid')
                    errors.append(result)
        self.assertEqual(errors, [errors[0]] * 4)

    def test_recovery_matching_real_and_dummy_handles_share_the_same_guess_budget(self):
        self.verified_account()
        challenge_id, actual_code = self.recovery()
        status, unavailable, _ = self.call('/auth/email-code',
                                            dict(email='unknown@example.test', purpose='reset_password'))
        self.assertEqual(status, 200)
        wrong = '000000' if actual_code != '000000' else '111111'
        for email, handle in (('recovery@example.test', challenge_id),
                               ('unknown@example.test', unavailable['challengeId'])):
            with self.subTest(email=email):
                for _ in range(20):
                    status, result, _ = self.reset(handle, wrong, email=email)
                    self.assertEqual(status, 400, result)
                    self.assertEqual(result['code'], 'email_code_invalid')
                status, result, headers = self.reset(handle, wrong, email=email)
                self.assertEqual(status, 429, result)
                self.assertEqual(result['code'], 'email_code_rate_limited')
                self.assertEqual(headers['Retry-After'], str(result['retryAfter']))
        with app.connection() as db:
            self.assertEqual([row[0] for row in db.execute('SELECT attempts FROM email_challenges')], [20, 20])

    def test_recovery_five_wrong_guesses_persist_and_disable_the_code(self):
        self.verified_account()
        challenge_id, code = self.recovery()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            status, error, _ = self.reset(challenge_id, wrong)
            self.assertEqual(status, 400, error)
            self.assertEqual(error['code'], 'email_code_invalid')
        app.initialize(recover=False)
        self.assertEqual(self.reset(challenge_id, code)[0], 400)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT attempts FROM email_challenges WHERE id=?', (challenge_id,)).fetchone()[0], 6)
        self.assertEqual(self.login()[0], 200)

    def test_recovery_codes_are_bound_to_email_and_cannot_cross_verification_purposes(self):
        uid = self.legacy('recovery@example.test')
        verify_id, verify_code = self.challenge('recovery@example.test', 'verify_email')
        with app.connection() as db:
            verification.mark_verified(db, uid)
            db.execute('DELETE FROM email_rate_events')
        reset_id, reset_code = self.recovery()
        self.assertEqual(self.reset(verify_id, verify_code)[0], 400)
        self.assertEqual(self.reset(reset_id, reset_code, email='different@example.test')[0], 400)
        # Even with the same owner and mailbox, a reset code cannot complete an
        # email-verification flow or register a new account.
        with app.connection() as db:
            db.execute('DELETE FROM verified_emails WHERE user_id=?', (uid,))
        status, error, _ = self.call('/auth/verify-email', dict(challengeId=reset_id, code=reset_code))
        self.assertEqual(status, 400, error)
        self.assertEqual(error['code'], 'email_code_invalid')
        self.assertEqual(self.register('different@example.test', reset_id, reset_code)[0], 400)
        with app.connection() as db:
            verification.mark_verified(db, uid)
        self.assertEqual(self.reset(reset_id, reset_code)[0], 200)

    def test_recovery_resending_supersedes_the_previous_code(self):
        self.verified_account()
        first_id, first_code = self.recovery()
        with app.connection() as db:
            db.execute('UPDATE email_rate_events SET occurred_at=occurred_at-61')
        second_id, second_code = self.recovery()
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.reset(first_id, first_code)[0], 400)
        self.assertEqual(self.reset(second_id, second_code)[0], 200)

    def test_recovery_simultaneous_consumption_succeeds_only_once(self):
        self.verified_account()
        challenge_id, code = self.recovery()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.reset(challenge_id, code, client=self.new_client()), range(2)))
        self.assertEqual(sorted(result[0] for result in results), [200, 400])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?', (challenge_id,)).fetchone()[0], 'consumed')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)

    def test_recovery_failure_rolls_back_password_challenge_and_session_revocation(self):
        uid = self.verified_account()
        challenge_id, code = self.recovery()
        with app.connection() as db:
            original = tuple(db.execute('SELECT salt,hash FROM users WHERE id=?', (uid,)).fetchone())
            db.execute("CREATE TRIGGER synthetic_session_failure BEFORE DELETE ON sessions "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic session failure'); END")
        self.assertEqual(self.reset(challenge_id, code)[0], 502)
        with app.connection() as db:
            self.assertEqual(tuple(db.execute('SELECT salt,hash FROM users WHERE id=?', (uid,)).fetchone()), original)
            self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?', (challenge_id,)).fetchone()[0], 'active')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (uid,)).fetchone()[0], 1)
            db.execute('DROP TRIGGER synthetic_session_failure')
        self.assertEqual(self.call('/auth/me')[0], 200)
        self.assertEqual(self.reset(challenge_id, code)[0], 200)

    def test_recovery_normal_password_change_invalidates_an_active_reset_code(self):
        self.verified_account()
        challenge_id, code = self.recovery()
        status, result, _ = self.call('/auth/password', dict(currentPassword=OLD_PASSWORD, newPassword='Changed-Normally-2026!'))
        self.assertEqual(status, 200, result)
        self.assertEqual(self.reset(challenge_id, code)[0], 400)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?', (challenge_id,)).fetchone()[0], 'superseded')
        self.assertEqual(self.call('/auth/me')[0], 200)
        self.assertEqual(self.login(password='Changed-Normally-2026!')[0], 200)

    def test_recovery_code_is_bound_to_the_password_version_even_if_it_remains_active(self):
        uid = self.verified_account()
        challenge_id, code = self.recovery()
        with app.connection() as db:
            version = db.execute('SELECT credential_version FROM email_reset_credentials WHERE challenge_id=?', (challenge_id,)).fetchone()[0]
            account = db.execute('SELECT salt,hash FROM users WHERE id=?', (uid,)).fetchone()
            self.assertNotIn(account['salt'], version)
            self.assertNotEqual(version, account['hash'])
            salt = '01' * 16
            db.execute('UPDATE users SET salt=?,hash=? WHERE id=?',
                       (salt, app.password_hash('Changed-Elsewhere-2026!', salt), uid))
        status, result, _ = self.reset(challenge_id, code)
        self.assertEqual(status, 400, result)
        self.assertEqual(result['code'], 'email_code_invalid')
        self.assertEqual(self.login(password='Changed-Elsewhere-2026!')[0], 200)

    def test_recovery_delivery_in_flight_cannot_reactivate_a_code_after_password_change(self):
        self.verified_account()
        entered, release = threading.Event(), threading.Event()

        def delayed_delivery(*message):
            self.mailbox.append(message)
            entered.set()
            if not release.wait(10):
                raise AssertionError('Synthetic delivery was not released')

        with patch.object(verification.mail_delivery, 'send_verification', side_effect=delayed_delivery), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.call, '/auth/email-code',
                                     dict(email='recovery@example.test', purpose='reset_password'), self.new_client())
            try:
                self.assertTrue(entered.wait(5))
                status, result, _ = self.call('/auth/password',
                                               dict(currentPassword=OLD_PASSWORD, newPassword='Changed-During-Send-2026!'))
                self.assertEqual(status, 200, result)
            finally:
                release.set()
            status, result, _ = future.result(timeout=10)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.reset(result['challengeId'], self.mailbox[-1][1])[0], 400)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT status FROM email_challenges WHERE id=?', (result['challengeId'],)).fetchone()[0], 'superseded')
        self.assertEqual(self.login(password='Changed-During-Send-2026!')[0], 200)

    def test_recovery_login_in_flight_cannot_create_a_session_with_the_previous_password(self):
        self.verified_account()
        challenge_id, code = self.recovery()
        entered, release = threading.Event(), threading.Event()
        original_hash = app.password_hash

        def delayed_password_hash(password, salt):
            result = original_hash(password, salt)
            if password == OLD_PASSWORD:
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Synthetic password check was not released')
            return result

        with patch.object(app, 'password_hash', side_effect=delayed_password_hash), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.login, client=self.new_client())
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.reset(challenge_id, code)[0], 200)
            finally:
                release.set()
            status, result, headers = future.result(timeout=10)
        self.assertEqual(status, 401, result)
        self.assertIsNone(headers.get('Set-Cookie'))
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
        self.assertEqual(self.login(password=NEW_PASSWORD)[0], 200)

    def test_recovery_revoked_session_cannot_finish_an_in_flight_normal_password_change(self):
        self.verified_account()
        challenge_id, code = self.recovery()
        entered, release = threading.Event(), threading.Event()
        original_user = app.Handler.user

        def delayed_user(handler, db):
            result = original_user(handler, db)
            if handler.path == '/api/auth/password' and not getattr(handler, '_recovery_test_checked', False):
                handler._recovery_test_checked = True
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Synthetic session check was not released')
            return result

        with patch.object(app.Handler, 'user', delayed_user), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.call, '/auth/password',
                                     dict(currentPassword=OLD_PASSWORD, newPassword='Stale-Change-2026!'))
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.reset(challenge_id, code, client=self.new_client())[0], 200)
            finally:
                release.set()
            status, result, _ = future.result(timeout=10)
        self.assertEqual(status, 401, result)
        self.assertEqual(self.login(password=NEW_PASSWORD)[0], 200)
        self.assertEqual(self.login(password='Stale-Change-2026!')[0], 401)

    def test_recovery_invalid_new_passwords_do_not_consume_a_valid_code(self):
        self.verified_account()
        challenge_id, code = self.recovery()
        for password in ('short', 'a' * 129, None, 12345678, []):
            with self.subTest(password=password):
                status, result, _ = self.reset(challenge_id, code, password=password)
                self.assertEqual(status, 400, result)
                with app.connection() as db:
                    self.assertEqual(tuple(db.execute('SELECT status,attempts FROM email_challenges WHERE id=?',
                                                       (challenge_id,)).fetchone()), ('active', 0))
        self.assertEqual(self.reset(challenge_id, code, password='a' * 128)[0], 200)
        self.assertEqual(self.login(password='a' * 128)[0], 200)

    def test_recovery_normalized_legacy_domains_reset_the_original_account_only(self):
        for index, (stored_domain, canonical_domain) in enumerate(LEGACY_EMAIL_DOMAINS):
            email = f'recovery-{index}@{stored_domain}'
            canonical = f'recovery-{index}@{canonical_domain}'
            with self.subTest(email=email):
                uid = self.verified_account(email)
                tasks = [dict(id=f'preserved-{index}', title='Existing task')]
                with app.connection() as db:
                    db.execute('INSERT INTO workspace VALUES(?,?,?,?)', (uid, 'tasks', json.dumps(tasks), 7))
                    verification_before = tuple(db.execute('SELECT * FROM verified_emails WHERE user_id=?',
                                                           (uid,)).fetchone())
                challenge_id, code = self.recovery(canonical, self.new_client())
                with app.connection() as db:
                    self.assertEqual(db.execute('SELECT user_id FROM email_challenges WHERE id=?',
                                                (challenge_id,)).fetchone()[0], uid)
                self.assertEqual(self.reset(challenge_id, code, email=email, client=self.new_client())[0], 200)
                self.assertEqual(self.call('/auth/me')[0], 401)
                self.assertEqual(self.login(email=email)[0], 401)
                status, result, _ = self.login(email=email, password=NEW_PASSWORD, client=self.client)
                self.assertEqual(status, 200, result)
                self.assertEqual(result['user']['id'], uid)
                self.assertEqual(result['user']['email'], email)
                self.assertTrue(result['user']['emailVerified'])
                self.assertEqual(self.call('/workspace')[1]['tasks'], dict(value=tasks, version=7))
                with app.connection() as db:
                    self.assertEqual(tuple(db.execute('SELECT * FROM verified_emails WHERE user_id=?',
                                                       (uid,)).fetchone()), verification_before)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], index + 1)

    def test_recovery_unicode_domain_uses_the_same_verified_account_and_canonical_recipient(self):
        canonical = 'student@xn--fsqu00a.xn--fiqs8s'
        uid = self.verified_account(canonical)
        email = ' Student@例子.中国 '
        challenge_id, code = self.recovery(email)
        self.assertEqual(self.mailbox[-1][0], canonical)
        status, result, _ = self.reset(challenge_id, code, email=email)
        self.assertEqual(status, 200, result)
        status, result, _ = self.login(email=email, password=NEW_PASSWORD)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['user']['id'], uid)
        self.assertEqual(result['user']['email'], canonical)

    def test_recovery_unicode_alabel_login_verification_and_reset_in_both_directions(self):
        for domain, canonical in (('faß.test', 'xn--fa-hia.test'), ('ος.test', 'xn--0xag.test')):
            for reverse in (False, True):
                local = f'old-{int(reverse)}'
                unicode_email, ascii_email = local + '@' + domain, local + '@' + canonical
                stored, alias = (ascii_email, unicode_email) if reverse else (unicode_email, ascii_email.upper())
                with self.subTest(domain=domain, reverse=reverse):
                    uid = self.legacy(stored)
                    status, account, _ = self.login(alias, client=self.client)
                    self.assertEqual(status, 200, account)
                    self.assertEqual(account['user']['id'], uid)
                    self.assertEqual(account['user']['email'], stored)
                    self.assertFalse(account['user']['emailVerified'])
                    status, challenge, _ = self.call('/auth/email-code', dict(purpose='verify_email', email='ignored@example.test'))
                    self.assertEqual(status, 200, challenge)
                    self.assertEqual(self.mailbox[-1][0], ascii_email)
                    self.assertEqual(self.call('/auth/verify-email', dict(challengeId=challenge['challengeId'], code=self.mailbox[-1][1]))[0], 200)
                    for requested, submitted in ((alias, stored), (stored, alias)):
                        with app.connection() as db:
                            db.execute('DELETE FROM email_rate_events')
                        challenge_id, code = self.recovery(requested)
                        self.assertEqual(self.mailbox[-1][0], ascii_email)
                        self.assertEqual(self.reset(challenge_id, code, email=submitted)[0], 200)
                    for address in (stored, alias):
                        status, result, _ = self.login(address, NEW_PASSWORD)
                        self.assertEqual(status, 200, result)
                        self.assertEqual(result['user']['id'], uid)
                        self.assertEqual(result['user']['email'], stored)

    def test_recovery_sharp_s_and_final_sigma_do_not_merge_distinct_domains(self):
        for index, domains in enumerate((('faß.test', 'fass.test'), ('ος.test', 'οσ.test'))):
            emails = [f'independent-{index}@{domain}' for domain in domains]
            canonical = ('xn--fa-hia.test', 'fass.test') if index == 0 else ('xn--0xag.test', 'xn--0xai.test')
            clients = [self.new_client(), self.new_client()]
            first_id = self.legacy(emails[0], clients[0])
            # The unrelated ASCII/sigma domain remains available for registration.
            status, _, _ = self.call('/auth/email-code', dict(email=emails[1], purpose='register'))
            self.assertEqual(status, 200)
            second_id = self.legacy(emails[1], clients[1])
            for selected, uid in enumerate((first_id, second_id)):
                other = (second_id, first_id)[selected]
                with self.subTest(domain=domains[selected]):
                    with app.connection() as db:
                        db.execute('DELETE FROM email_rate_events')
                        before = dict(db.execute('SELECT * FROM users WHERE id=?', (other,)).fetchone())
                        other_verified = verification.verified(db, other)
                    status, challenge, _ = self.call('/auth/email-code', dict(purpose='verify_email'), clients[selected])
                    self.assertEqual(status, 200, challenge)
                    self.assertEqual(self.mailbox[-1][0], f'independent-{index}@{canonical[selected]}')
                    self.assertEqual(self.call('/auth/verify-email', dict(challengeId=challenge['challengeId'], code=self.mailbox[-1][1]), clients[selected])[0], 200)
                    with app.connection() as db:
                        db.execute('DELETE FROM email_rate_events')
                    challenge_id, code = self.recovery(emails[selected])
                    self.assertEqual(self.reset(challenge_id, code, email=emails[1-selected])[0], 400)
                    self.assertEqual(self.reset(challenge_id, code, email=emails[selected])[0], 200)
                    with app.connection() as db:
                        self.assertEqual(dict(db.execute('SELECT * FROM users WHERE id=?', (other,)).fetchone()), before)
                        self.assertEqual(verification.verified(db, other), other_verified)
                    self.assertEqual(self.call('/auth/me', client=clients[1-selected])[0], 200 if selected == 0 else 401)
                    self.assertEqual(self.login(emails[selected], NEW_PASSWORD)[1]['user']['id'], uid)

    def test_recovery_pre_fix_wrong_domain_codes_cannot_be_consumed(self):
        email, wrong_email = 'old@faß.test', 'old@fass.test'
        uid = self.legacy(email)
        normalize = verification.normalize_email
        def old_normalize(value):
            return normalize(value.replace('faß.test', 'fass.test'))
        # Simulate challenges issued by the old IDNA2003 implementation.
        with patch.object(verification, 'normalize_email', side_effect=old_normalize):
            status, challenge, _ = self.call('/auth/email-code', dict(purpose='verify_email'))
            self.assertEqual(status, 200, challenge)
            verify_code = self.mailbox[-1][1]
            self.assertEqual(self.mailbox[-1][0], wrong_email)
            with app.connection() as db:
                verification.mark_verified(db, uid)
                db.execute('DELETE FROM email_rate_events')
            reset_id, reset_code = self.recovery(email)
        other_uid = self.verified_account(wrong_email, self.new_client())
        with app.connection() as db:
            other_before = dict(db.execute('SELECT * FROM users WHERE id=?', (other_uid,)).fetchone())
            db.execute('DELETE FROM verified_emails WHERE user_id=?', (uid,))
        self.assertEqual(self.call('/auth/verify-email', dict(challengeId=challenge['challengeId'], code=verify_code))[0], 400)
        with app.connection() as db:
            verification.mark_verified(db, uid)
        for address in (email, wrong_email, 'old@xn--fa-hia.test'):
            self.assertEqual(self.reset(reset_id, reset_code, email=address)[0], 400)
        self.assertEqual(self.login(email)[1]['user']['id'], uid)
        with app.connection() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM users WHERE id=?', (other_uid,)).fetchone()), other_before)


def load_tests(loader, standard_tests, pattern):
    # Reuse the HTTP fixture without running its inherited verification tests a
    # second time when unittest discovers this module.
    return unittest.TestSuite(PasswordRecoveryTests(name)
                              for name in loader.getTestCaseNames(PasswordRecoveryTests)
                              if name.startswith('test_recovery_'))


if __name__ == '__main__':
    unittest.main()
