"""Account-scoped email list filtering and ordering tests."""
import json
import sqlite3
import unittest

import email_import
import email_source
import recognition
import app
from types import SimpleNamespace
from unittest.mock import patch


ADDRESS = 'student@stu.pku.edu.cn'


class EmailListFilterTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        recognition.initialize(self.db)
        email_import.initialize(self.db, recover=False)
        self.db.execute('CREATE TABLE email_source_credentials(user_id TEXT PRIMARY KEY,address TEXT,username TEXT,secret BLOB)')
        self.db.execute('INSERT INTO email_source_credentials VALUES(?,?,?,?)', ('owner', ADDRESS, 'user', b'x'))

    def tearDown(self):
        self.db.close()

    def cache(self, uid, status='no_tasks'):
        input_id = email_source._identity(ADDRESS, '77', uid)
        request_id = email_import._request_id(input_id)
        result = {'status': status, 'tasks': [{'title': 'task'}] if status == 'success' else []}
        request = {'records': [{'input_id': input_id}]}
        self.db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,?)',
                        ('owner', request_id, 'fingerprint', json.dumps(request), json.dumps(result), 0))
        return input_id, request_id

    def test_attention_and_pending_review_sort_across_full_range_and_scope_candidates(self):
        for uid in range(2, 62):
            self.cache(uid)
        target, request_id = self.cache(1, 'success')
        self.db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate,state) VALUES(?,?,?,?,?)',
                        ('owner-pending', 'owner', request_id, json.dumps({'input_id': target, 'title': 'review'}), 'pending'))
        self.db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate,state) VALUES(?,?,?,?,?)',
                        ('other-pending', 'other-account', request_id, json.dumps({'input_id': target, 'title': 'private'}), 'pending'))
        excluded = email_source._identity(ADDRESS, '77', 2)
        self.db.execute('INSERT INTO email_import_exclusions VALUES(?,?,1)', ('owner', excluded))

        select = email_import._email_list_selection(self.db, 'owner', 'all', 'attention')
        ordered = select('77', tuple(range(61, 0, -1)))
        self.assertEqual(ordered[0], 1)
        self.assertEqual(ordered[-1], 2)
        other_mail = email_source._identity(ADDRESS, '77', 3)
        request_map = {target: request_id, other_mail: request_id}
        self.assertEqual(email_import._pending_review_counts(
            self.db, 'owner', [target, other_mail], request_map), {target: 1})
        pending = email_import._email_list_selection(self.db, 'owner', 'pending_review', 'attention')
        self.assertEqual(pending('77', tuple(range(61, 0, -1))), [1])
        self.db.execute("UPDATE recognition_candidates SET state='ignored' WHERE id='owner-pending'")
        self.assertEqual(email_import._pending_review_counts(
            self.db, 'owner', [target, other_mail], request_map), {})
        self.assertEqual(pending('77', tuple(range(61, 0, -1))), [])
        excluded_filter = email_import._email_list_selection(self.db, 'owner', 'excluded', 'attention')
        self.assertEqual(excluded_filter('77', tuple(range(61, 0, -1))), [2])

    def test_projection_preserves_cache_and_exclusions_over_stale_job_progress(self):
        cached, _ = self.cache(1)
        excluded, _ = self.cache(2, 'success')
        failed = email_source._identity(ADDRESS, '77', 3)
        self.db.execute('INSERT INTO email_import_exclusions VALUES(?,?,1)', ('owner', excluded))
        items = [dict(input_id=x, state='failed', task_count=0, request_id=None,
                      error='synthetic failure', cached=False) for x in (cached, excluded, failed)]
        email_import._insert_job(self.db, 'owner', 'job', 'operation', 'failed',
                                 dict(items=items))
        # Another account's later progress must never mask this account's state.
        email_import._insert_job(self.db, 'other', 'job', 'operation', 'running',
            dict(items=[dict(items[2], state='processing')]))
        projection = email_import._message_states(self.db, 'owner', [cached, excluded, failed])
        self.assertEqual(projection[cached]['state'], 'no_tasks')
        self.assertIsNone(projection[cached]['error'])
        self.assertEqual(projection[excluded]['state'], 'excluded')
        self.assertTrue(projection[excluded]['excluded'])
        self.assertEqual(projection[failed]['state'], 'failed')
        self.assertEqual(projection[failed]['error'], 'synthetic failure')
        select = email_import._email_list_selection(self.db, 'owner', 'failed', 'newest')
        self.assertEqual(select('77', (3, 2, 1)), [3])

    def test_uid_order_can_be_oldest_or_newest(self):
        for uid in (2, 7, 11):
            self.cache(uid)
        descending = email_import._email_list_selection(self.db, 'owner', 'all', 'newest')
        ascending = email_import._email_list_selection(self.db, 'owner', 'all', 'oldest')
        uids = (11, 7, 2)
        self.assertEqual(descending('77', uids), [11, 7, 2])
        self.assertEqual(ascending('77', uids), [2, 7, 11])


    def test_invalid_filter_is_rejected_before_mailbox_preview(self):
        handler = SimpleNamespace(command='POST', reply=lambda *_args: None)
        with patch.object(email_source, 'preview_range') as preview:
            for payload in ({'filter': 'invalid'}, {'sort': 'random'}):
                with self.assertRaises(app.ApiError) as error:
                    email_import.route(handler, self.db, 'owner', '/api/sources/email/preview',
                        payload, app.ApiError, '/tmp/no-key', '/tmp/no-db')
                self.assertEqual(error.exception.status, 400)
        preview.assert_not_called()

    def test_job_state_projection_chunks_ids_below_sqlite_parameter_limit(self):
        ids = [f'email:id:{i}' for i in range(1100)]
        items = [{'input_id': value, 'state': 'queued', 'task_count': None,
                  'request_id': None, 'error': None} for value in ids]
        email_import._insert_job(self.db, 'owner', 'job', 'operation', 'queued', {'items': items})
        states = email_import._job_states(self.db, 'owner', ids)
        self.assertEqual(len(states), len(ids))
        self.assertTrue(all(value['state'] == 'queued' for value in states.values()))


if __name__ == '__main__':
    unittest.main()
