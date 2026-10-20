import json
import os
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
from unittest.mock import patch

import app
from auth_test_support import registration_payload
import error_reports

CASES = json.loads((Path(__file__).resolve().parent.parent / 'contracts/examples/error-reports-v01.json').read_text())['reports']


class ErrorReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = app.DB
        app.DB = Path(self.temp.name) / 'db.sqlite3'
        self.env = patch.dict(os.environ, NIUMA_ENVIRONMENT='test', NIUMA_ADMIN_USER_IDS='', NIUMA_COOKIE_SECURE='0')
        self.env.start()
        error_reports._attempts.clear()
        app.initialize(recover=False)
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        app.DB = self.old
        self.env.stop()
        self.temp.cleanup()

    def call(self, path, body=None, headers=None):
        if path == '/auth/register':
            body = registration_payload(body, lambda request: self.call('/auth/email-code', request, headers))
        request = Request(f'http://127.0.0.1:{self.server.server_port}/api' + path,
                          None if body is None else json.dumps(body).encode(),
                          {'Content-Type': 'application/json', **(headers or {})})
        try:
            response = self.client.open(request)
        except HTTPError as error:
            response = error
        return response.status, json.load(response)

    def test_anonymous_submission_and_lost_response_retry_save_one_report(self):
        status, receipt = self.call('/error-reports', CASES[0])
        self.assertEqual(status, 200)
        self.assertTrue(receipt['saved'])
        self.assertEqual(self.call('/error-reports', CASES[0]), (200, receipt))
        with app.connection() as db:
            rows = db.execute('SELECT * FROM error_reports').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['actor'], '')
            self.assertNotIn('127.0.0.1', json.dumps(dict(rows[0])))

    def test_validation_rejects_private_unknown_and_invalid_fields(self):
        for field, value in [('message', 'PRIVATE-CONTENT'), ('password', 'PRIVATE-SECRET'), ('action', 'private-task-title'), ('code', 'private-stack'), ('status', True), ('frontend_revision', 'https://private.test'), ('occurred_at', 'invalid')]:
            with self.subTest(field=field):
                body = {**CASES[0], field: value}
                self.assertEqual(self.call('/error-reports', body)[0], 400)
        self.assertEqual(self.call('/error-reports', {**CASES[0], 'extra': 'x' * 5000})[0], 413)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM error_reports').fetchone()[0], 0)

    def test_changed_payload_cannot_reuse_an_incident(self):
        self.assertEqual(self.call('/error-reports', CASES[0])[0], 200)
        self.assertEqual(self.call('/error-reports', {**CASES[0], 'code': 'unknown'})[0], 409)

    def test_rate_limit_does_not_prevent_a_confirmed_retry(self):
        with patch.object(error_reports, '_LIMIT', 2):
            first = self.call('/error-reports', CASES[0])
            self.assertEqual(self.call('/error-reports', CASES[1])[0], 200)
            self.assertEqual(self.call('/error-reports', {**CASES[0], 'incident_id': '10000000-0000-4000-8000-000000000003'})[0], 429)
            self.assertEqual(self.call('/error-reports', CASES[0]), first)

    def test_cross_origin_submissions_are_rejected(self):
        self.assertEqual(self.call('/error-reports', CASES[0], {'Origin': 'https://untrusted.test'})[0], 403)

    def test_only_admins_can_read_reports_in_existing_dashboard(self):
        self.call('/error-reports', CASES[0])
        _, result = self.call('/auth/register', dict(name='Synthetic', email='error-report@example.test', password='Synthetic-2026!'))
        uid = result['user']['id']
        self.assertEqual(self.call('/admin/usage')[0], 401)
        self.assertEqual(self.call('/admin/auth/login', dict(email='error-report@example.test', password='Synthetic-2026!'))[0], 403)
        with patch.dict(os.environ, NIUMA_ADMIN_USER_IDS=uid):
            self.assertEqual(self.call('/admin/auth/login', dict(email='error-report@example.test', password='Synthetic-2026!'))[0], 200)
            status, dashboard = self.call('/admin/usage')
            self.assertEqual(status, 200)
            self.assertEqual(len(dashboard['errorReports']), 1)
            self.assertEqual(dashboard['errorReports'][0]['code'], CASES[0]['code'])
            self.assertNotIn('actor', dashboard['errorReports'][0])
            self.assertEqual(self.call('/admin/usage?start=2000-01-01&end=2000-01-02')[1]['errorReports'], [])

    def test_account_submission_does_not_store_identity_or_private_contents(self):
        self.call('/auth/register', dict(name='PRIVATE-NAME', email='private@example.test', password='Private-2026!'))
        self.assertEqual(self.call('/error-reports', CASES[1])[0], 200)
        with app.connection() as db:
            row = dict(db.execute('SELECT * FROM error_reports').fetchone())
            self.assertTrue(row['actor'])
            encoded = json.dumps(row)
            for private in ['PRIVATE', 'private@example.test', 'Private-2026!', '127.0.0.1']:
                self.assertNotIn(private, encoded)

    def test_retention_cleans_old_reports(self):
        self.call('/error-reports', CASES[0])
        with app.connection() as db:
            db.execute('UPDATE error_reports SET created_at="2000-01-01T00:00:00+08:00"')
        self.call('/error-reports', CASES[1])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM error_reports').fetchone()[0], 1)
