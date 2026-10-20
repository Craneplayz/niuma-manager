"""User actions atomically accept pending drafts and create ordinary tasks."""
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
import usage
from auth_test_support import registration_payload


class ReviewTaskActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB
        app.DB = Path(self.temp.name) / 'review.sqlite3'
        self.env = patch.dict(os.environ, NIUMA_ENVIRONMENT='test', NIUMA_COOKIE_SECURE='0')
        self.env.start()
        app.initialize(recover=False)
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))
        account = registration_payload(dict(name='Synthetic', email='review@example.test', password='Review-Test-2026!'),
            lambda body: self.call('/auth/email-code', body))
        self.uid = self.call('/auth/register', account)[1]['user']['id']
        fixture = json.loads((Path(__file__).parent.parent / 'contracts/text-to-app/text-contract-v0.2.json').read_text())
        candidate = fixture['expected']['tasks'][0]
        candidate.update(needs_review=True, review_reasons=['需要用户确认'])
        with app.connection() as db:
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,?)', (self.uid, fixture['request']['request_id'], 'synthetic', json.dumps(fixture['request']), json.dumps(fixture['expected']), 0))
            db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)', ('pending', self.uid, fixture['request']['request_id'], json.dumps(candidate)))
        self.body = dict(action='create', version=1, tasks_version=0, target_id=None, reviewed=True,
                         draft=dict(title=candidate['title'], description=candidate['description'], course=None, content_type='task', due=None))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        app.DB = self.old_db
        self.env.stop()
        self.temp.cleanup()

    def call(self, path, body=None):
        request = Request(f'http://127.0.0.1:{self.server.server_port}/api{path}',
                          None if body is None else json.dumps(body).encode(), {'Content-Type': 'application/json'})
        try:
            response = self.client.open(request)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def snapshot(self):
        with app.connection() as db:
            row = db.execute('SELECT * FROM workspace WHERE user_id=? AND name="tasks"', (self.uid,)).fetchone()
            candidate = dict(db.execute('SELECT state,version FROM recognition_candidates WHERE id="pending"').fetchone())
            return json.loads(row['value']) if row else [], row['version'] if row else 0, candidate

    def test_completion_accepts_once_and_enters_completed_with_source_history(self):
        self.body['complete'] = True
        for _ in range(2):
            self.assertEqual(self.call('/recognition/candidates/pending/resolve', self.body)[0], 200)
        tasks, version, candidate = self.snapshot()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(version, 1)
        self.assertEqual(candidate, dict(state='applied', version=2))
        self.assertEqual(tasks[0]['status'], 'completed')
        self.assertEqual(tasks[0]['completedAt'], usage.datetime.now(usage.TZ).date().isoformat())
        self.assertEqual(tasks[0]['recognitionHistory'][0]['storageId'], 'pending')
        self.assertEqual(len(tasks[0]['recognitionHistory']), 1)
        self.assertTrue(tasks[0]['source'])

    def test_edit_saves_user_fields_as_an_ordinary_unfinished_task(self):
        self.body['draft'].update(title='用户修改的任务', description='用户写的说明', course='数据结构', due=dict(date='2026-10-25', precision='date'))
        self.assertEqual(self.call('/recognition/candidates/pending/resolve', self.body)[0], 200)
        tasks, _, candidate = self.snapshot()
        self.assertEqual(candidate['state'], 'applied')
        self.assertEqual(tasks[0]['title'], '用户修改的任务')
        self.assertEqual(tasks[0]['due'], dict(date='2026-10-25', precision='date'))
        self.assertEqual(tasks[0]['status'], 'not_started')
        self.assertEqual(tasks[0]['description'], '用户写的说明')

    def test_invalid_completion_and_unreviewed_or_automatic_actions_never_apply(self):
        for changes in [dict(complete='true'), dict(complete=1), dict(complete=True, reviewed=False),
                        dict(complete=True, auto_apply=True), dict(complete=True, action='ignore')]:
            with self.subTest(changes=changes):
                self.assertEqual(self.call('/recognition/candidates/pending/resolve', {**self.body, **changes})[0], 400)
                self.assertEqual(self.snapshot(), ([], 0, dict(state='pending', version=1)))

    def test_stale_workspace_or_candidate_version_keeps_draft_pending(self):
        for changes in [dict(version=0), dict(tasks_version=99)]:
            self.assertEqual(self.call('/recognition/candidates/pending/resolve', {**self.body, **changes, 'complete': True})[0], 409)
            self.assertEqual(self.snapshot(), ([], 0, dict(state='pending', version=1)))

    def test_failure_after_task_write_rolls_back_both_task_and_candidate_then_retries(self):
        self.body['complete'] = True
        with patch('submission_runtime.candidate_applied', side_effect=RuntimeError('synthetic rollback')):
            self.assertEqual(self.call('/recognition/candidates/pending/resolve', self.body)[0], 502)
        self.assertEqual(self.snapshot(), ([], 0, dict(state='pending', version=1)))
        self.assertEqual(self.call('/recognition/candidates/pending/resolve', self.body)[0], 200)
        self.assertEqual(self.snapshot()[0][0]['status'], 'completed')

    def test_reading_does_not_confirm_and_other_accounts_cannot_accept_the_draft(self):
        self.assertEqual(self.call('/recognition/batches')[0], 200)
        self.assertEqual(self.snapshot(), ([], 0, dict(state='pending', version=1)))
        self.call('/auth/register', dict(name='Other', email='other-review@example.test', password='Review-Test-2026!'))
        self.assertEqual(self.call('/recognition/candidates/pending/resolve', self.body)[0], 404)
        self.assertEqual(self.snapshot(), ([], 0, dict(state='pending', version=1)))
