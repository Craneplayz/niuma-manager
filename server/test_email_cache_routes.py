"""Header-cache HTTP integration checks; temporary databases and synthetic mail only."""
import unittest
from unittest.mock import patch

import app
import email_import
import source_sync
import email_test_support as acceptance


class EmailCacheRouteTests(acceptance.EmailHTTPTestCase):

    def test_restore_and_paging_do_not_decrypt_or_refetch_cached_headers(self):
        with patch.object(source_sync, 'cipher', side_effect=AssertionError('must stay cached')):
            status, empty = self.call('/sources/email/preview', dict(limit=61, cache_only=True))
        self.assertEqual(status, 200, empty)
        self.assertTrue(empty['cache']['miss'])
        self.assertEqual(self.imap_calls, 0)
        self.assertEqual(self.model.call_count, 0)
        status, first = self.call('/sources/email/preview', dict(limit=61, refresh=True))
        self.assertEqual(status, 200, first)
        token = first['cache']['snapshot_id']
        self.assertTrue(first['cache']['synced_at'])
        self.assertEqual(len(first['messages']), 50)
        self.assertEqual(len(self.imap.fetches), 50)
        with patch.object(source_sync, 'cipher', side_effect=AssertionError('must stay cached')):
            status, restored = self.call('/sources/email/preview', dict(limit=61, cache_only=True, snapshot_id=token))
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored['messages'], first['messages'])
        self.assertEqual(self.imap_calls, 1)
        status, second = self.call('/sources/email/preview', dict(limit=61, offset=50, snapshot_id=token))
        self.assertEqual(status, 200, second)
        self.assertEqual(len(second['messages']), 11)
        self.assertEqual(len(self.imap.fetches), 61)
        with patch.object(source_sync, 'cipher', side_effect=AssertionError('must stay cached')):
            status, revisited = self.call('/sources/email/preview', dict(limit=61, offset=50, snapshot_id=token))
        self.assertEqual(status, 200, revisited)
        self.assertEqual(self.imap_calls, 2)
        self.assertEqual(self.model.call_count, 0)
        self.assertTrue(all('HEADER.FIELDS' in query for _, query in self.imap.fetches))

    def test_dynamic_flags_and_policy_are_projected_on_header_cache_hits(self):
        _, first = self.call('/sources/email/preview', dict(limit=50, refresh=True))
        input_id = first['messages'][0]['input_id']
        original = self.settled(self.start([input_id], 'cached-state')['job_id'])
        self.assertEqual(original['state'], 'succeeded')
        calls = self.imap_calls
        self.call('/sources/email/exclusions', dict(input_ids=[input_id], excluded=True))
        with patch.object(email_import, 'current_policy_version', return_value='policy-current'):
            status, restored = self.call('/sources/email/preview', dict(limit=50, cache_only=True))
        self.assertEqual(status, 200, restored)
        self.assertEqual(self.imap_calls, calls)
        row = next(row for row in restored['messages'] if row['input_id'] == input_id)
        self.assertEqual(row['state'], 'excluded')
        self.call('/sources/email/exclusions', dict(input_ids=[input_id], excluded=False))
        with patch.object(email_import, 'current_policy_version', return_value='policy-current'):
            restored = self.call('/sources/email/preview', dict(limit=50, cache_only=True))[1]
        row = next(row for row in restored['messages'] if row['input_id'] == input_id)
        self.assertEqual(row['state'], 'no_tasks')
        self.assertEqual(row['policy_status'], 'unknown')
        self.assertEqual(self.model.call_count, 1)

    def test_disconnect_and_mailbox_change_clear_headers_and_reject_old_tokens(self):
        _, first = self.call('/sources/email/preview', dict(refresh=True))
        token = first['cache']['snapshot_id']
        self.assertEqual(self.call('/sources/email/credentials/remove', {})[0], 200)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM email_header_snapshots').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM email_cached_headers').fetchone()[0], 0)
        self.call('/sources/email/credentials', dict(address='other@pku.edu.cn', username='other',
            password='Synthetic-only', consent=True))
        status, result = self.call('/sources/email/preview', dict(cache_only=True, snapshot_id=token))
        self.assertEqual(status, 409, result)
        self.assertEqual(self.imap_calls, 1)

    def test_invalid_cache_controls_are_rejected_without_mailbox_access(self):
        for payload in ({'refresh': 'yes'}, {'cache_only': 1}, {'snapshot_id': {}},
                        {'snapshot_id': 'x' * 200}, {'refresh': True, 'cache_only': True},
                        {'load_all': 'yes'}, {'load_all': True, 'offset': 25}):
            with self.subTest(payload=payload):
                status, result = self.call('/sources/email/preview', payload)
                self.assertEqual(status, 400, result)
        self.assertEqual(self.imap_calls, 0)

    def test_full_range_headers_load_once_in_bounded_batches_and_restore_without_imap(self):
        with patch.object(self.imap,'uid',wraps=self.imap.uid) as commands:
            status,full=self.call('/sources/email/preview',dict(limit=61,page_size=25,load_all=True,refresh=True))
        self.assertEqual(status,200,full); self.assertEqual(len(full['messages']),61)
        self.assertIsNone(full['next_offset']); self.assertTrue(full['cache']['complete'])
        batches=[call.args for call in commands.call_args_list if call.args[0]=='FETCH' and ',' in str(call.args[1]) and 'HEADER.FIELDS' in call.args[2]]
        self.assertEqual(len(batches),2)
        self.assertTrue(all(len(args[1].split(','))<=50 for args in batches))
        self.assertTrue(all('BODY.PEEK[HEADER.FIELDS' in args[2] for args in batches))
        self.assertEqual(self.imap_calls,1); self.assertTrue(self.imap.readonly)
        with patch.object(source_sync,'cipher',side_effect=AssertionError('cache restore must not decrypt')):
            status,restored=self.call('/sources/email/preview',dict(limit=61,load_all=True,cache_only=True,snapshot_id=full['cache']['snapshot_id']))
        self.assertEqual(status,200,restored); self.assertEqual(restored['messages'],full['messages'])
        self.assertEqual(self.imap_calls,1); self.assertEqual(self.model.call_count,0)

    def test_full_range_cache_only_returns_partial_headers_without_hydrating(self):
        status,first=self.call('/sources/email/preview',dict(limit=61,page_size=25,refresh=True))
        self.assertEqual(status,200,first)
        with patch.object(source_sync,'cipher',side_effect=AssertionError('must remain cache-only')):
            status,partial=self.call('/sources/email/preview',dict(limit=61,load_all=True,cache_only=True))
        self.assertEqual(status,200,partial); self.assertEqual(len(partial['messages']),25)
        self.assertEqual(partial['summary']['total'],61); self.assertFalse(partial['cache']['complete'])
        self.assertIsNone(partial['next_offset']); self.assertEqual(self.imap_calls,1)


if __name__ == '__main__':
    unittest.main()
