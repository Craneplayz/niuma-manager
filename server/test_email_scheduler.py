"""Synthetic determinism checks for account-fair email dispatch."""
import threading
import os
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import email_scheduler


class FairDispatcherTests(unittest.TestCase):
    def test_shutdown_cancels_all_waiters_without_dispatching_them(self):
        dispatcher = email_scheduler.FairDispatcher(global_limit=1, account_limit=1)
        gate, entered = threading.Event(), threading.Event()
        def block():
            entered.set()
            if not gate.wait(5):
                raise RuntimeError('synthetic test timeout')
        active = dispatcher.submit('active', block)
        queued = []
        try:
            self.assertTrue(entered.wait(2))
            callback = mock.Mock()
            queued = [dispatcher.submit(account, callback) for account in ('first','first','second')]
            dispatcher.shutdown(wait=False)
            self.assertTrue(all(future.cancelled() for future in queued))
            self.assertEqual(dispatcher.stats()['queued'], 0)
            callback.assert_not_called()
        finally:
            gate.set()
            active.result(timeout=3)
            dispatcher.shutdown()
        self.assertEqual(dispatcher.stats()['running'], 0)

    def test_thirty_accounts_are_served_round_robin_with_two_slots_each(self):
        dispatcher = email_scheduler.FairDispatcher(global_limit=8, account_limit=2)
        blocker = threading.Event()
        started = threading.Event()
        first = dispatcher.submit('blocker-0', lambda: (started.set(), blocker.wait(5)))
        self.assertTrue(started.wait(2))
        # Fill all remaining global slots so all per-account requests are queued
        # before the fair rotation begins.
        blockers = [dispatcher.submit(f'blocker-{n}', lambda: blocker.wait(5)) for n in range(1, 8)]
        deadline = time.monotonic() + 2
        while dispatcher.stats()['running'] != 8 and time.monotonic() < deadline:
            time.sleep(.001)
        order = []
        lock = threading.Lock()
        release = threading.Event()
        futures = []
        for account in range(30):
            for _ in range(2):
                def work(account=account):
                    with lock: order.append(account)
                    release.wait(5)
                futures.append(dispatcher.submit(f'account-{account}', work))
        self.assertEqual(dispatcher.stats()['queued'], 60)
        blocker.set()
        deadline = time.monotonic() + 5
        while len(order) < 8 and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertEqual(order[:8], list(range(8)))
        self.assertEqual(dispatcher.stats()['running'], 8)
        release.set()
        while len(order) < 30 and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertEqual(order[:30], list(range(30)))
        for future in [first, *blockers, *futures]:
            future.result(timeout=5)
        dispatcher.shutdown()

    def test_cancelled_queued_future_does_not_consume_capacity(self):
        dispatcher = email_scheduler.FairDispatcher(global_limit=1, account_limit=1)
        blocker = threading.Event()
        started = threading.Event()
        first = dispatcher.submit('held', lambda: (started.set(), blocker.wait(5)))
        self.assertTrue(started.wait(2))
        cancelled = dispatcher.submit('cancelled', lambda: self.fail('cancelled work ran'))
        ran = threading.Event()
        next_future = dispatcher.submit('next', ran.set)
        self.assertTrue(cancelled.cancel())
        blocker.set()
        first.result(timeout=3)
        next_future.result(timeout=3)
        self.assertTrue(ran.is_set())
        self.assertEqual(dispatcher.stats()['running'], 0)
        self.assertEqual(dispatcher.stats()['queued'], 0)
        dispatcher.shutdown()

    def test_cancelling_head_of_line_item_wakes_eligible_same_account_work(self):
        dispatcher = email_scheduler.FairDispatcher(global_limit=2, account_limit=2)
        blocker = threading.Event()
        started = threading.Event()
        active = dispatcher.submit('same', lambda: (started.set(), blocker.wait(5)), units=1)
        self.assertTrue(started.wait(2))
        impossible_head = dispatcher.submit('same', lambda: None, units=2)
        eligible_next = threading.Event()
        tail = dispatcher.submit('same', eligible_next.set, units=1)
        self.assertEqual(dispatcher.stats()['running'], 1)
        self.assertEqual(dispatcher.stats()['queued'], 2)
        self.assertTrue(impossible_head.cancel())
        self.assertTrue(eligible_next.wait(2), 'cancellation must re-pump the account queue')
        blocker.set()
        active.result(timeout=3)
        tail.result(timeout=3)
        dispatcher.shutdown()

    def test_executor_submission_failure_releases_weighted_slots(self):
        dispatcher = email_scheduler.FairDispatcher(global_limit=2, account_limit=2)
        def reject(*_args, **_kwargs):
            raise RuntimeError('synthetic submit rejection')
        dispatcher.executor.submit = reject
        with self.assertRaisesRegex(RuntimeError, 'synthetic submit rejection'):
            dispatcher.submit('one', lambda: None, units=2).result(timeout=2)
        self.assertEqual(dispatcher.stats()['running'], 0)
        self.assertEqual(dispatcher.stats()['queued'], 0)
        dispatcher.shutdown()


class ProcessOwnerTests(unittest.TestCase):
    def test_real_live_owner_survives_probe_and_second_instance(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            self.assertTrue(email_scheduler._pid_alive(child.pid))
            self.assertIsNone(child.poll(), 'liveness probing must not terminate the owner')
            with tempfile.TemporaryDirectory() as temp:
                path = os.path.join(temp, 'owner.sqlite3')
                db = self.owner_db(path, socket.gethostname(), child.pid)
                try:
                    self.assertFalse(email_scheduler.register_process_owner(db, path))
                finally:
                    db.close()
                self.assertFalse(email_scheduler.claim_process_owner(path))
                self.assertIsNone(child.poll())
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        self.assertFalse(email_scheduler._pid_alive(child.pid))

    def owner_db(self, path, host, pid, heartbeat=None):
        db = sqlite3.connect(path)
        db.execute('''CREATE TABLE email_import_scheduler_owner(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), host TEXT NOT NULL,
            pid INTEGER NOT NULL, heartbeat REAL NOT NULL)''')
        db.execute('INSERT INTO email_import_scheduler_owner VALUES(1,?,?,?)',
                   (host, pid, time.time() if heartbeat is None else heartbeat))
        db.commit()
        return db

    def test_fresh_foreign_lease_fences_local_claim_and_stale_lease_can_be_taken_over(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'owner.sqlite3')
            db = self.owner_db(path, 'another-host', os.getpid() + 100)
            db.close()
            self.assertFalse(email_scheduler.claim_process_owner(path))
            db = sqlite3.connect(path)
            db.execute('UPDATE email_import_scheduler_owner SET heartbeat=? WHERE singleton=1',
                       (time.time() - 60,))
            db.commit(); db.close()
            self.assertTrue(email_scheduler.claim_process_owner(path))
            db = sqlite3.connect(path)
            self.assertTrue(email_scheduler.is_current_owner(db))
            db.close()

    def test_fresh_dead_local_pid_is_reclaimed_but_fresh_live_pid_is_not(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'owner.sqlite3')
            db = self.owner_db(path, socket.gethostname(), 2_000_000_000)
            self.assertTrue(email_scheduler.register_process_owner(db, path))
            self.assertTrue(email_scheduler.is_current_owner(db))
            db.close()

        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'owner.sqlite3')
            db = self.owner_db(path, socket.gethostname(), os.getpid() + 1)
            with mock.patch.object(email_scheduler, '_pid_alive', return_value=True):
                self.assertFalse(email_scheduler.register_process_owner(db, path))
            db.close()

    def test_claim_does_not_create_missing_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'missing.sqlite3')
            self.assertFalse(email_scheduler.claim_process_owner(path))
            self.assertFalse(os.path.exists(path))

    def test_heartbeat_removes_local_ownership_after_database_disappears(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'removed.sqlite3')
            thread = threading.Thread(target=email_scheduler._heartbeat,
                args=(path, socket.gethostname(), os.getpid()), daemon=True)
            with email_scheduler._owner_lock:
                email_scheduler._owned_paths.add(path)
                email_scheduler._owner_threads[path] = thread
            with mock.patch.object(email_scheduler.time, 'sleep', return_value=None):
                thread.start(); thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertFalse(email_scheduler.owns_process(path))
            with email_scheduler._owner_lock:
                self.assertNotIn(path, email_scheduler._owner_threads)


if __name__ == '__main__':
    unittest.main()
