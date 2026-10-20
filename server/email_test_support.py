"""Shared synthetic email fixtures; no real credentials, IMAP or model calls."""
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from email.header import Header
from http.cookiejar import CookieJar
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import app
import email_import
import email_source
import recognition

ADDRESS = 'synthetic@stu.pku.edu.cn'
FINAL = {'succeeded', 'partial_success', 'failed', 'stopped', 'interrupted'}

class FakeIMAP:
    def __init__(self, uids=(7,), body=b'Please submit the assignment.', structure=None):
        self.uids = tuple(uids)
        self.body = body
        self.structure = structure or '("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 30 1)'
        self.sock = None
        self.readonly = None
        self.fetches = []
        self.login_args = None
        self.logged_out = False

    def login(self, username, password):
        self.login_args = (username, password)
        return 'OK', [b'logged in']

    def logout(self):
        self.logged_out = True
        return 'BYE', [b'logged out']

    def select(self, mailbox, readonly=False):
        self.readonly = readonly
        return 'OK', [str(len(self.uids)).encode()]

    def response(self, name):
        self.asserted_response = name
        return 'UIDVALIDITY', [b'77']

    def uid(self, command, *args):
        if command == 'SEARCH':
            return 'OK', [b' '.join(str(uid).encode() for uid in self.uids)]
        if command != 'FETCH':
            raise AssertionError(f'unexpected IMAP command: {command}')
        if args[1] == '(UID INTERNALDATE)':
            values = []
            for value in str(args[0]).split(','):
                uid = int(value)
                self.fetches.append((uid, args[1]))
                instant = getattr(self, 'dates', {}).get(uid, '14-Oct-2026 10:00:00 +0800')
                values.append(f'{uid} (UID {uid} INTERNALDATE "{instant}")'.encode())
            return 'OK', values
        if ',' in str(args[0]) and 'HEADER.FIELDS' in args[1]:
            return 'OK', [item for value in str(args[0]).split(',') for item in self.uid('FETCH',value,args[1])[1]]
        uid, query = int(args[0]), args[1]
        self.fetches.append((uid, query))
        if 'HEADER.FIELDS' in query:
            subject = Header(f'作业通知 {uid}', 'utf-8').encode()
            headers = (
                f'Date: Wed, 14 Oct 2026 10:00:00 +0800\r\n'
                f'From: teacher@example.edu.cn\r\n'
                f'Subject: {subject}\r\n\r\n'
            ).encode('utf-8')
            instant = getattr(self, 'dates', {}).get(uid, '14-Oct-2026 10:00:00 +0800')
            metadata = f'(UID {uid} INTERNALDATE "{instant}")'.encode()
            return 'OK', [(metadata, headers)]
        if 'BODYSTRUCTURE' in query:
            metadata = f'(UID {uid} BODYSTRUCTURE {self.structure})'.encode()
            # No IMAP literal: imaplib returns this FETCH response as bytes.
            return 'OK', [metadata]
        if 'BODY.PEEK[TEXT]' in query or 'BODY.PEEK[1]' in query:
            return 'OK', [(f'(UID {uid} BODY[1] {{{len(self.body)}}})'.encode(), self.body)]
        raise AssertionError(f'unexpected FETCH query: {query}')


def no_tasks(request, _error=None):
    return dict(contract_version='0.2', request_id=request['request_id'], status='no_tasks',
                record_results=[dict(input_id=r['input_id'], status='no_tasks', task_ids=[], warnings=[])
                                for r in request['records']], tasks=[], errors=[])


class EmailHTTPTestCase(unittest.TestCase):
    """Fresh authenticated app with a cancellable fake mailbox and model."""
    address = ADDRESS
    mail_uids = range(1, 62)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old_db = app.DB
        app.DB = Path(self.temp.name) / 'email-test.sqlite3'
        self.addCleanup(setattr, app, 'DB', old_db)
        environment = patch.dict(os.environ, NIUMA_ENVIRONMENT='test',
            NIUMA_COOKIE_SECURE='0', NIUMA_BACKGROUND_RECOVERY='0',
            NIUMA_ENABLE_SCHEDULED_SYNC='0', NIUMA_EMAIL_CONCURRENCY='2')
        environment.start()
        self.addCleanup(environment.stop)
        app.initialize(recover=False)
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        # Join request handlers before removing their SQLite database. A response
        # can arrive just before the handler exits its database context.
        self.server.daemon_threads = False
        thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        def close_server():
            self.server.shutdown()
            self.server.server_close()
            thread.join()
        self.addCleanup(close_server)
        self.client = self.account('owner')
        self.imap = FakeIMAP(uids=self.mail_uids)
        self.imap_calls = 0

        @contextmanager
        def mailbox(*_args, cancel_check=None, **_kwargs):
            self.imap_calls += 1
            if cancel_check:
                cancel_check()
            yield self.mailbox_client()

        self.mailbox_patch = patch.object(email_source, '_mailbox', mailbox)
        self.mailbox_patch.start()
        self.addCleanup(self.mailbox_patch.stop)
        self.model_patch = patch.object(recognition, 'recognize', side_effect=no_tasks)
        self.model = self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.addCleanup(email_import.wait_for_jobs, app.DB)
        self.assertEqual(self.call('/sources/email/credentials', dict(address=self.address,
            username='synthetic', password='Synthetic-client-password',
            consent=True, process_all=False))[0], 200)

    def mailbox_client(self):
        return self.imap

    def call(self, path, body=None, client=None):
        request = Request(f'http://127.0.0.1:{self.server.server_port}/api' + path,
                          None if body is None else json.dumps(body).encode(),
                          {'Content-Type': 'application/json'})
        try:
            response = (client or self.client).open(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def account(self, name):
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        status, _ = self.call('/auth/register', dict(name=name, email=name+'@acceptance.test',
            password='Synthetic-only-2026!'), client=client)
        self.assertEqual(status, 200)
        return client

    def settled(self, job_id, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, job = self.call('/sources/email/jobs/' + job_id)
            self.assertEqual(status, 200, job)
            if job['state'] in FINAL:
                return job
            time.sleep(.02)
        self.fail(f'synthetic email job did not reach a terminal state: {job}')

    def start(self, ids, operation):
        status, job = self.call('/sources/email/jobs', dict(operation_id=operation, input_ids=ids))
        self.assertEqual(status, 202, job)
        return job
