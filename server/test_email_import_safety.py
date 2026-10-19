"""Safety regressions for persistent email consent at shared model admission.

All mailbox/model edges are synthetic. These tests fill the real process model
queue and call the application route functions directly, so they need no socket.
"""
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
from email_test_support import no_tasks
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

try:
    import app
    import email_import
    import email_source
    import recognition
    import source_sync
    from email_test_support import FakeIMAP
except ImportError:
    from server import app, email_import, email_source, recognition, source_sync
    from server.email_test_support import FakeIMAP


ADDRESS = 'synthetic@stu.pku.edu.cn'
FINAL = {'succeeded', 'partial_success', 'failed', 'stopped', 'interrupted'}




class _Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def read(self, _limit=-1): return self.body


class _Handler:
    def __init__(self, command='POST', path=''):
        self.command, self.path, self.response = command, path, None
    def reply(self, status, value, *_args):
        self.response = (status, value)
        return self.response


class EmailImportSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB
        app.DB = Path(self.temp.name) / 'safety.sqlite3'
        self.environment = patch.dict(os.environ, NIUMA_ENVIRONMENT='test',
            NIUMA_COOKIE_SECURE='0', NIUMA_BACKGROUND_RECOVERY='0', NIUMA_ENABLE_SCHEDULED_SYNC='0',
            TEXT_HARNESS_URL='http://synthetic.invalid/model')
        self.environment.start()
        app.initialize(recover=False)
        self.uid, self.other_uid = 'synthetic-owner', 'synthetic-other'
        self.imap = FakeIMAP(uids=range(1, 62))

        @contextmanager
        def mailbox(*_args, cancel_check=None, **_kwargs):
            if cancel_check:
                cancel_check()
            yield self.imap

        self.mailbox_patch = patch.object(email_source, '_mailbox', mailbox)
        self.mailbox_patch.start()
        self.provider_calls = 0

        def provider(request, **_kwargs):
            self.provider_calls += 1
            payload = json.loads(request.data.decode())
            return _Response(json.dumps(no_tasks(payload)).encode())

        self.provider_patch = patch.object(recognition, 'urlopen', side_effect=provider)
        self.provider_patch.start()
        self._source_call('/api/sources/email/credentials', dict(address=ADDRESS,
            username='synthetic', password='Synthetic-client-password', consent=True, process_all=False))
        queue = recognition._ACTIVE
        self.old_limit = queue.limit
        self.held_slots = []

    def tearDown(self):
        self.release_queue()
        email_import.wait_for_jobs(app.DB)
        self.mailbox_patch.stop()
        self.provider_patch.stop()
        app.DB = self.old_db
        self.environment.stop()
        self.temp.cleanup()

    def _connect(self):
        db = sqlite3.connect(app.DB, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _email_call(self, path, body=None, uid=None, command='POST'):
        uid = uid or self.uid
        handler = _Handler(command, path)
        with self._connect() as db:
            result = email_import.route(handler, db, uid, path, body or {}, app.ApiError,
                                        app.DB.parent / 'credential.key', app.DB)
        return result, handler.response

    def _source_call(self, path, body=None, uid=None):
        handler = _Handler('POST', path)
        with self._connect() as db:
            result = source_sync.route(handler, db, uid or self.uid, path, body or {},
                                       app.ApiError, app.DB.parent / 'credential.key')
        return result, handler.response

    def _recognition_call(self, path, body=None, uid=None, command='POST'):
        handler = _Handler(command, path)
        with self._connect() as db:
            result = recognition.route(handler, db, uid or self.uid, path, body or {},
                                       app.ApiError, email_import.recognition_guard)
        return result, handler.response

    def _job(self, job_id):
        with self._connect() as db:
            row = db.execute('SELECT snapshot FROM email_import_jobs WHERE user_id=? AND job_id=?',
                             (self.uid, job_id)).fetchone()
        return json.loads(row['snapshot'])

    def settled(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = self._job(job_id)
            if job['state'] in FINAL: return job
            time.sleep(.02)
        self.fail('synthetic email job did not reach a terminal state')

    def release_queue(self):
        queue = recognition._ACTIVE
        while self.held_slots:
            self.held_slots.pop()
            queue.release()
        with queue.condition:
            queue.limit = self.old_limit
            queue.condition.notify_all()

    def saturated_start(self, operation):
        queue = recognition._ACTIVE
        while queue.acquire(blocking=False): self.held_slots.append(True)
        self.assertTrue(self.held_slots, 'test must occupy the shared model queue')
        input_id = email_source._identity(ADDRESS, '77', 61)
        _, response = self._email_call('/api/sources/email/jobs',
            dict(operation_id=operation, input_ids=[input_id]))
        self.assertEqual(response[0], 202, response)
        job_id = response[1]['job_id']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snapshot = self._job(job_id)
            if snapshot.get('waiting_reason') == 'model_capacity' and email_import.email_scheduler.stats()['running'] == 0:
                with queue.condition:
                    self.assertEqual(len(queue.waiters),1,'email keeps one FIFO ticket while releasing execution capacity')
                return job_id, input_id
            if snapshot['state'] in FINAL:
                self.fail(f'job terminated before model admission: {snapshot}')
            time.sleep(.01)
        self.fail('email worker never waited for shared model admission')

    def mutate_while_queued(self, mutation):
        job_id, input_id = self.saturated_start('cancel-' + mutation)
        if mutation == 'consent':
            _, response = self._email_call('/api/sources/email/preferences',
                dict(consent=False, process_all=False))
        elif mutation == 'exclusion':
            _, response = self._email_call('/api/sources/email/exclusions',
                dict(input_ids=[input_id], excluded=True))
        elif mutation == 'credential':
            # Same mailbox/login, rotated secret: consent remains granted while
            # the persisted credential identity changes.
            _, response = self._source_call('/api/sources/email/credentials', dict(address=ADDRESS,
                username='synthetic', password='Rotated-client-password'))
        else:
            raise AssertionError(mutation)
        self.assertEqual(response[0], 200, response)
        queue = recognition._ACTIVE
        deadline = time.monotonic() + 2
        cancelled = False
        while time.monotonic() < deadline:
            if self._job(job_id)['state'] in FINAL:
                cancelled = True
                break
            time.sleep(.02)
        self.release_queue()
        terminal = self.settled(job_id)
        self.assertTrue(cancelled, f'{mutation} did not cancel queued model admission')
        self.assertEqual(self.provider_calls, 0, f'{mutation} still reached the fake provider')
        with self._connect() as db:
            self.assertFalse(db.execute('SELECT 1 FROM recognition_batches').fetchone())
            self.assertFalse(db.execute('SELECT 1 FROM email_import_body_snapshots WHERE user_id=?',(self.uid,)).fetchone(),
                             'revocation, rotation and exclusion must purge staged bodies')
        return terminal

    def test_consent_revocation_cancels_waiting_email_model(self):
        self.mutate_while_queued('consent')

    def test_email_exclusion_cancels_waiting_email_model(self):
        self.mutate_while_queued('exclusion')

    def test_credential_rotation_cancels_waiting_email_model(self):
        self.mutate_while_queued('credential')

    def test_shared_model_capacity_resumes_without_rereading_body(self):
        job_id,input_id = self.saturated_start('resume-shared-model')
        body_reads = len([query for _,query in self.imap.fetches if 'BODY.PEEK[1]' in query or 'BODY.PEEK[TEXT]' in query])
        self.assertEqual(body_reads,1)
        self.assertEqual(self.provider_calls,0)
        self.release_queue()
        final = self.settled(job_id)
        self.assertEqual(final['state'],'succeeded',final)
        self.assertEqual(self.provider_calls,1)
        self.assertEqual(len([query for _,query in self.imap.fetches if 'BODY.PEEK[1]' in query or 'BODY.PEEK[TEXT]' in query]),1)

    def _email_request(self):
        input_id = email_source._identity(ADDRESS, '77', 61)
        record = dict(text_quality='complete', platform='email',
            published_at=dict(value='2026-10-14T10:00:00+08:00', precision='datetime', basis='source'),
            collected_at='2026-10-14T10:00:00+08:00', timezone='Asia/Shanghai', timezone_basis='source',
            source_url=None, source_label='Synthetic notice', source_item_id='77:61',
            course_or_project=None, context=[], upstream_hints=None, input_id=input_id,
            text='Synthetic body for policy testing')
        return dict(contract_version='0.2', request_id='email:safety:retry', records=[record])

    def test_generic_retry_and_batch_endpoints_enforce_email_consent_and_ownership(self):
        request = self._email_request()
        failed = dict(contract_version='0.2', request_id=request['request_id'], status='failed',
            record_results=[dict(input_id=request['records'][0]['input_id'], status='failed', task_ids=[], warnings=[])],
            tasks=[], errors=[])
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self._connect() as db:
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,0)',
                (self.uid, request['request_id'], fingerprint, json.dumps(request), json.dumps(failed)))

        self._email_call('/api/sources/email/preferences', dict(consent=False, process_all=False))
        with self.assertRaises(app.ApiError) as revoked:
            self._recognition_call('/api/recognition/retry', request)
        self.assertEqual(revoked.exception.status, 403)
        self.assertEqual(self.provider_calls, 0)

        _, response = self._recognition_call('/api/recognition/batches', uid=self.other_uid, command='GET')
        self.assertEqual(response[0], 200)
        self.assertFalse(response[1], 'another account must not read the email recognition batch')
        with self.assertRaises(app.ApiError):
            self._recognition_call('/api/recognition/batches', request, uid=self.other_uid)
        self.assertEqual(self.provider_calls, 0)


if __name__ == '__main__':
    unittest.main()
