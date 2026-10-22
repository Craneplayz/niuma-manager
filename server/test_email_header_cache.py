import os
import tempfile
import unittest
import threading
import time
from unittest.mock import patch

import email_header_cache as cache
import email_source

ADDRESS = 'person@pku.edu.cn'


class FakeIMAP:
    uidvalidity = '10'
    uids = [5, 4, 3]
    fetches = []
    sessions = 0
    missing = set()

    def __init__(self, *args, **kwargs):
        type(self).sessions += 1
        self.sock = None
        self.selected_validity = type(self).uidvalidity
    def login(self, *_): return 'OK', []
    def select(self, *_args, **_kwargs): return 'OK', []
    def response(self, *_): return 'UIDVALIDITY', [self.selected_validity.encode()]
    def uid(self, op, *args):
        if op == 'SEARCH': return 'OK', [' '.join(map(str, self.uids)).encode()]
        if op == 'FETCH':
            if ',' in str(args[0]):
                return 'OK', [item for value in str(args[0]).split(',') for item in self.uid('FETCH',value,args[1])[1]]
            uid = int(args[0]); type(self).fetches.append(uid)
            if 'HEADER.FIELDS' in args[1]:
                if uid in type(self).missing: return 'OK', []
                return 'OK', [(f'{uid} (UID {uid} INTERNALDATE "01-Jan-2026 00:00:00 +0000")'.encode(),
                               b'Date: Thu, 01 Jan 2026 00:00:00 +0000\r\nFrom: Sender <s@example.org>\r\nSubject: Subject ' + str(uid).encode() + b'\r\n\r\n')]
            return 'OK', []
        return 'OK', []
    def logout(self): return 'BYE', []


class HeaderCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'cache.sqlite')
        FakeIMAP.fetches = []; FakeIMAP.sessions = 0; FakeIMAP.uidvalidity = '10'; FakeIMAP.uids = [5, 4, 3]; FakeIMAP.missing = set()
        self.imap = patch.object(email_source.imaplib, 'IMAP4_SSL', FakeIMAP)
        self.imap.start()
        self.loader = lambda: ('person', 'synthetic')
    def tearDown(self):
        self.imap.stop(); self.tmp.cleanup()
    def preview(self, **kwargs):
        return cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1,
                             password_loader=self.loader, **kwargs)

    def test_delta_sync_and_missing_page_fetch_only_that_page(self):
        self.preview(offset=0, refresh=True)
        FakeIMAP.uids = [6, 5, 4, 3]
        refreshed = self.preview(offset=0, refresh=True)
        self.assertEqual(FakeIMAP.fetches, [5, 6])
        token = refreshed['cache']['snapshot_id']
        self.preview(offset=1, snapshot_id=token)
        self.assertEqual(FakeIMAP.fetches, [5, 6])

    def test_empty_range_and_scope_isolation(self):
        FakeIMAP.uids = []
        result = self.preview(offset=0, refresh=True)
        self.assertEqual(result['messages'], [])
        other = cache.preview(self.path, 'user-2', ADDRESS, limit=3, page_size=1, cache_only=True)
        self.assertTrue(other['cache']['miss'])

    def test_uidvalidity_change_invalidates_old_token(self):
        first = self.preview(offset=0, refresh=True)
        FakeIMAP.uidvalidity = '11'
        second = self.preview(offset=0, refresh=True)
        self.assertNotEqual(first['cache']['snapshot_id'], second['cache']['snapshot_id'])
        with self.assertRaises(email_source.EmailSourceError) as caught:
            self.preview(offset=0, snapshot_id=first['cache']['snapshot_id'])
        self.assertEqual(caught.exception.status, 409)

    def test_failed_refresh_retains_prior_snapshot(self):
        first = self.preview(offset=0, refresh=True)
        with patch.object(email_source, '_search_uids', side_effect=email_source.EmailSourceError(502, 'failed')):
            with self.assertRaises(email_source.EmailSourceError):
                self.preview(offset=0, refresh=True)
        reread = cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1)
        self.assertEqual(reread['cache']['snapshot_id'], first['cache']['snapshot_id'])

    def test_cached_revisit_survives_reload_and_lazy_page_does_not_search(self):
        first = self.preview(offset=0, refresh=True)
        __import__('importlib').reload(cache)
        before = FakeIMAP.sessions
        cached = cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1, offset=0)
        self.assertEqual(cached['cache']['synced_at'], first['cache']['synced_at'])
        self.assertEqual(cached['messages'],first['messages'])
        self.assertEqual(FakeIMAP.sessions,before)
        self.assertFalse(cached['cache']['cache_only'])
        restored = cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1, offset=0, cache_only=True)
        self.assertTrue(restored['cache']['cache_only'])
        searches = []
        original = email_source._search_uids
        with patch.object(email_source, '_search_uids', side_effect=lambda *a, **k: searches.append(1) or original(*a, **k)):
            page = cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1, offset=1,
                                 password_loader=self.loader, snapshot_id=first['cache']['snapshot_id'])
        self.assertEqual(searches, [])
        self.assertEqual(page['messages'][0]['subject'], 'Subject 4')
        self.assertEqual(page['cache']['synced_at'], first['cache']['synced_at'])
        self.assertEqual(FakeIMAP.sessions, before + 1)

    def test_lazy_uidvalidity_mismatch_does_not_search_or_write(self):
        first = self.preview(offset=0, refresh=True)
        FakeIMAP.uidvalidity = '11'
        searches = []
        with patch.object(email_source, '_search_uids', side_effect=lambda *a, **k: searches.append(1)):
            with self.assertRaises(email_source.EmailSourceError) as caught:
                cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1, offset=1,
                              password_loader=self.loader, snapshot_id=first['cache']['snapshot_id'])
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(searches, [])
        old = cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1, offset=0,
                            cache_only=True, snapshot_id=first['cache']['snapshot_id'])
        self.assertEqual(old['messages'][0]['input_id'], first['messages'][0]['input_id'])

    def test_refresh_invalidates_other_ranges_on_uidvalidity_change(self):
        first = self.preview(offset=0, refresh=True)
        FakeIMAP.uidvalidity = '11'
        cache.preview(self.path, 'user-1', ADDRESS, limit=2, page_size=1, offset=0,
                      refresh=True, password_loader=self.loader)
        with self.assertRaises(email_source.EmailSourceError) as caught:
            cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1, offset=0,
                          snapshot_id=first['cache']['snapshot_id'])
        self.assertEqual(caught.exception.status, 409)

    def test_missing_header_is_reported_incomplete(self):
        FakeIMAP.missing = {5}
        result = self.preview(offset=0, refresh=True)
        self.assertFalse(result['cache']['complete'])
        self.assertTrue(result['cache']['miss'])
        self.assertEqual(result['messages'], [])

    def test_slow_initial_refresh_cannot_overwrite_newer_refresh(self):
        entered, release = threading.Event(), threading.Event()
        calls = {'count': 0}
        def delayed(client, start_date=None):
            calls['count'] += 1
            if calls['count'] == 1:
                entered.set()
                self.assertTrue(release.wait(3))
                return [3]
            return [5, 4, 3]
        outcomes = []
        def slow():
            try:
                outcomes.append(cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1,
                    refresh=True, password_loader=self.loader))
            except Exception as exc:
                outcomes.append(exc)
        second = []
        def refresh_again():
            try:
                second.append(cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1,
                    refresh=True, password_loader=self.loader))
            except Exception as exc:
                second.append(exc)
        with patch.object(email_source, '_search_uids', side_effect=delayed):
            worker = threading.Thread(target=slow)
            worker.start()
            self.assertTrue(entered.wait(3))
            duplicate = threading.Thread(target=refresh_again)
            duplicate.start()
            time.sleep(.05)
            release.set()
            worker.join(3); duplicate.join(3)
        self.assertFalse(worker.is_alive() or duplicate.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], dict)
        self.assertEqual(len(second), 1)
        self.assertIsInstance(second[0], dict)
        current = cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1, cache_only=True)
        self.assertEqual(current['cache']['snapshot_id'], outcomes[0]['cache']['snapshot_id'])

    def test_old_cross_range_refresh_cannot_erase_new_uid_epoch(self):
        entered, release = threading.Event(), threading.Event()
        calls = {'count': 0}
        def delayed(client, start_date=None):
            calls['count'] += 1
            if calls['count'] == 1:
                entered.set()
                self.assertTrue(release.wait(3))
            return [5, 4, 3]
        outcome = []
        def old_refresh():
            try:
                outcome.append(cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1,
                    refresh=True, password_loader=self.loader))
            except Exception as exc:
                outcome.append(exc)
        newer_outcome = []
        def newer_refresh():
            try:
                newer_outcome.append(cache.preview(self.path, 'user-1', ADDRESS, limit=2, page_size=1,
                    refresh=True, password_loader=self.loader))
            except Exception as exc:
                newer_outcome.append(exc)
        with patch.object(email_source, '_search_uids', side_effect=delayed):
            worker = threading.Thread(target=old_refresh)
            worker.start()
            self.assertTrue(entered.wait(3))
            FakeIMAP.uidvalidity = '11'
            newer_worker = threading.Thread(target=newer_refresh)
            newer_worker.start()
            time.sleep(.05)
            release.set()
            worker.join(3); newer_worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], dict)
        self.assertEqual(len(newer_outcome), 1)
        self.assertIsInstance(newer_outcome[0], email_source.EmailSourceError)
        self.assertEqual(newer_outcome[0].status, 409)
        restored = cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1, cache_only=True)
        self.assertEqual(restored['cache']['snapshot_id'], outcome[0]['cache']['snapshot_id'])
        self.assertEqual(restored['messages'][0]['input_id'], outcome[0]['messages'][0]['input_id'])

    def test_stale_guard_prevents_publication(self):
        with self.assertRaises(email_source.EmailSourceError) as caught:
            cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1,
                          refresh=True, password_loader=self.loader, guard=lambda _db: False)
        self.assertEqual(caught.exception.status, 409)
        self.assertTrue(cache.preview(self.path, 'user-1', ADDRESS, limit=3, page_size=1,
                                      cache_only=True)['cache']['miss'])

    def test_concurrent_same_range_refreshes_share_one_mailbox_sync(self):
        entered, release = threading.Event(), threading.Event()
        original = email_source._search_uids
        outcomes = []
        def delayed(client, start_date=None):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(client, start_date)
        def run():
            try:
                outcomes.append(cache.preview(self.path, 'user-1', ADDRESS, limit=3,
                    page_size=1, refresh=True, password_loader=self.loader))
            except Exception as error:
                outcomes.append(error)
        with patch.object(email_source, '_search_uids', side_effect=delayed):
            first = threading.Thread(target=run); first.start()
            self.assertTrue(entered.wait(2))
            second = threading.Thread(target=run); second.start()
            time.sleep(.05)
            release.set(); first.join(3); second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(isinstance(value, dict) for value in outcomes), outcomes)
        self.assertEqual(FakeIMAP.sessions, 1)
        self.assertEqual(cache._sync_locks, {})

    def test_failed_coalesced_refresh_does_not_suppress_waiter_retry(self):
        entered, release = threading.Event(), threading.Event()
        calls = {'count': 0}
        outcomes = []
        original = email_source._search_uids
        def maybe_fail(client, start_date=None):
            calls['count'] += 1
            if calls['count'] == 1:
                entered.set()
                self.assertTrue(release.wait(3))
                raise email_source.EmailSourceError(502, 'synthetic failure')
            return original(client, start_date)
        def run():
            try:
                outcomes.append(cache.preview(self.path, 'user-1', ADDRESS, limit=3,
                    page_size=1, refresh=True, password_loader=self.loader))
            except Exception as error:
                outcomes.append(error)
        with patch.object(email_source, '_search_uids', side_effect=maybe_fail):
            first = threading.Thread(target=run); first.start()
            self.assertTrue(entered.wait(2))
            second = threading.Thread(target=run); second.start()
            time.sleep(.05)
            release.set(); first.join(3); second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(calls['count'], 2)
        self.assertEqual(sum(isinstance(value, dict) for value in outcomes), 1)
        self.assertEqual(FakeIMAP.sessions, 2)
        self.assertEqual(cache._sync_locks, {})


if __name__ == '__main__':
    unittest.main()
