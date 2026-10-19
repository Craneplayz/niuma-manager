"""Deterministic synthetic coverage for bounded email message dispatch."""
import email_test_support as support
import json
import os
import threading
import time
import unittest
from http.cookiejar import CookieJar
from unittest.mock import patch
from urllib.request import HTTPCookieProcessor, build_opener

import app
import email_import
import email_scheduler
import email_source
from email_test_support import ADDRESS, no_tasks
from email_test_support import FakeIMAP


class EmailImportParallelTests(support.EmailHTTPTestCase):
    def mailbox_client(self):
        # Concurrent workers receive independent sessions, as in real IMAP.
        return FakeIMAP(uids=self.mail_uids)


    def ids(self, count):
        return [email_source._identity(ADDRESS, '77', 20 + i) for i in range(count)]

    def test_two_in_flight_out_of_order_results_and_counts(self):
        entered = [threading.Event() for _ in range(4)]
        release = [threading.Event() for _ in range(4)]
        lock = threading.Lock(); active = 0; maximum = 0; starts = []
        def model(request, error):
            nonlocal active, maximum
            index = int(request['records'][0]['input_id'].rsplit(':', 1)[1]) - 20
            with lock:
                starts.append(index); active += 1; maximum = max(maximum, active)
            entered[index].set(); release[index].wait(5)
            with lock: active -= 1
            return no_tasks(request, error)
        self.model.side_effect = model
        ids = self.ids(4); job = self.start(ids, 'parallel-order')
        try:
            self.assertTrue(entered[0].wait(5)); self.assertTrue(entered[1].wait(5))
            time.sleep(.05)
            self.assertEqual(len(starts), 2, 'dispatcher must not submit the whole selection')
            release[1].set()
            self.assertTrue(entered[2].wait(5), 'completion should replenish one slot')
            release[2].set(); release[0].set()
            self.assertTrue(entered[3].wait(5)); release[3].set()
        finally:
            for event in release: event.set()
        final = self.settled(job['job_id'])
        self.assertEqual(maximum, 2)
        self.assertEqual(final['counts']['processed'], 4, final)
        self.assertEqual(final['counts']['pending'], 0, final)

    def test_shared_message_pool_is_capped_at_eight(self):
        entered = threading.Event()
        release = threading.Event()
        active = 0; maximum = 0; starts = 0; lock = threading.Lock()
        def model(request, error):
            nonlocal active, maximum, starts
            with lock:
                active += 1; starts += 1; maximum = max(maximum, active)
                if active == 8: entered.set()
            release.wait(5)
            with lock: active -= 1
            return no_tasks(request, error)
        self.model.side_effect = model
        clients = [self.client]
        for n in (1, 2):
            client = build_opener(HTTPCookieProcessor(CookieJar()))
            self.client = client
            self.call('/auth/register', dict(name=f'p{n}', email=f'p{n}@test.invalid', password='Synthetic-only-2026!'))
            self.call('/sources/email/credentials', dict(address=ADDRESS, username='synthetic',
                password='Synthetic-only-mailbox-password', consent=True, process_all=False))
            clients.append(client)
        jobs = []
        try:
            with patch.dict(os.environ, NIUMA_EMAIL_CONCURRENCY='4'):
                for n, client in enumerate(clients):
                    self.client = client
                    jobs.append((client, self.start(self.ids(4), f'eight-cap-{n}')))
                self.assertTrue(entered.wait(5))
                time.sleep(.05)
                with lock:
                    self.assertEqual(starts, 8, 'the real shared executor must hold the ninth message')
                    self.assertEqual(maximum, 8)
                release.set()
                email_import.wait_for_jobs(app.DB)
        finally:
            release.set(); self.client = clients[0]
        for client, job in jobs:
            self.client = client
            final = self.settled(job['job_id'])
            self.assertEqual(final['state'], 'succeeded', final)
            self.assertEqual(final['counts']['processed'], 4, final)
        self.assertEqual(starts, 12)
        self.assertEqual(maximum, 8)

    def test_stop_during_first_wave_drains_and_never_dispatches_more(self):
        entered = [threading.Event(), threading.Event()]; release = threading.Event()
        starts = []; lock = threading.Lock()
        def model(request, error):
            with lock: starts.append(request['records'][0]['input_id'])
            entered[len(starts)-1].set(); release.wait(5)
            return no_tasks(request, error)
        self.model.side_effect = model
        job = self.start(self.ids(5), 'stop-wave')
        try:
            self.assertTrue(entered[0].wait(5)); self.assertTrue(entered[1].wait(5))
            self.assertEqual(self.call('/sources/email/jobs/' + job['job_id'] + '/stop', {})[0], 200)
        finally: release.set()
        final = self.settled(job['job_id'])
        self.assertEqual(final['state'], 'stopped')
        self.assertEqual(len(starts), 2)
        self.assertFalse(self.call('/recognition/batches')[1])

    def test_exclusion_during_first_wave_blocks_replenishment_and_late_save(self):
        entered = [threading.Event(), threading.Event()]; release = threading.Event()
        starts = []; lock = threading.Lock()
        def model(request, error):
            with lock:
                index = len(starts); starts.append(request['records'][0]['input_id'])
            if index < 2:
                entered[index].set(); release.wait(5)
            return no_tasks(request, error)
        self.model.side_effect = model
        ids = self.ids(4); job = self.start(ids, 'exclude-wave')
        try:
            self.assertTrue(entered[0].wait(5)); self.assertTrue(entered[1].wait(5))
            self.assertEqual(self.call('/sources/email/exclusions', dict(input_ids=[ids[0]], excluded=True))[0], 200)
        finally: release.set()
        final = self.settled(job['job_id'])
        self.assertEqual(final['state'], 'succeeded', final)
        self.assertEqual(final['counts']['excluded'], 1)
        self.assertEqual(final['counts']['processed'], 3)
        self.assertEqual(len(starts), 4)
        batches = self.call('/recognition/batches')[1]
        self.assertFalse(any(ids[0] in json.dumps(batch) for batch in batches))

    def test_revocation_and_credential_rotation_drain_wave_without_replenishment(self):
        for mutation in ('revoke', 'credential'):
            with self.subTest(mutation=mutation):
                entered = [threading.Event(), threading.Event()]
                release = threading.Event(); starts = []; lock = threading.Lock()
                def model(request, error):
                    with lock:
                        index = len(starts); starts.append(request['records'][0]['input_id'])
                    entered[index].set(); release.wait(5)
                    return no_tasks(request, error)
                self.model.side_effect = model
                job = self.start(self.ids(5), 'invalidate-wave-' + mutation)
                try:
                    self.assertTrue(entered[0].wait(5)); self.assertTrue(entered[1].wait(5))
                    if mutation == 'revoke':
                        status, _ = self.call('/sources/email/preferences', dict(consent=False, process_all=False))
                    else:
                        status, _ = self.call('/sources/email/credentials', dict(address=ADDRESS,
                            username='synthetic', password='Rotated-synthetic-password'))
                    self.assertEqual(status, 200)
                finally:
                    release.set()
                final = self.settled(job['job_id'])
                self.assertNotEqual(final['state'], 'succeeded', final)
                self.assertEqual(len(starts), 2)
                self.assertFalse(self.call('/recognition/batches')[1])
                # Restore consent and the original credential for the next case.
                if mutation == 'revoke':
                    self.call('/sources/email/preferences', dict(consent=True, process_all=False))
                else:
                    self.call('/sources/email/credentials', dict(address=ADDRESS,
                        username='synthetic', password='Synthetic-only-mailbox-password', consent=True, process_all=False))

    def test_failed_item_is_dispatched_again_by_a_new_job(self):
        input_id = self.ids(1)[0]
        calls = 0
        def transient(request, error):
            nonlocal calls
            calls += 1
            if calls == 1: raise RuntimeError('synthetic transient failure')
            return no_tasks(request, error)
        self.model.side_effect = transient
        first = self.settled(self.start([input_id], 'failed-first')['job_id'])
        self.assertEqual(first['items'][0]['state'], 'failed', first)
        second = self.settled(self.start([input_id], 'failed-retry')['job_id'])
        self.assertEqual(second['items'][0]['state'], 'no_tasks', second)
        self.assertEqual(self.model.call_count, 2)

    def test_submit_failure_drains_and_records_inflight_success_before_settlement(self):
        entered = threading.Event(); release = threading.Event()
        def blocked_model(request, error):
            entered.set()
            release.wait(5)
            return no_tasks(request, error)
        self.model.side_effect = blocked_model
        dispatcher = email_scheduler.dispatcher()
        original_submit = dispatcher.submit
        submitted = 0
        def fail_third_submit(*args, **kwargs):
            nonlocal submitted
            submitted += 1
            if submitted == 3:
                raise RuntimeError('synthetic dispatcher submission failure')
            return original_submit(*args, **kwargs)
        ids = self.ids(4)
        # Reject replenishment after two individual messages entered the pool;
        # both results must be drained before terminal settlement.
        with patch.object(dispatcher, 'submit', side_effect=fail_third_submit):
            job = self.start(ids, 'submit-failure-drain')
            try:
                self.assertTrue(entered.wait(5), 'first dispatched message should enter the model')
                snapshot = self.call('/sources/email/jobs/' + job['job_id'])[1]
                self.assertEqual(snapshot['state'], 'running', 'terminal settlement must wait for in-flight work')
            finally:
                release.set()
            final = self.settled(job['job_id'])
        self.assertEqual(final['state'], 'partial_success', final)
        self.assertEqual(final['counts']['processed'], 2, final)
        self.assertEqual(final['counts']['failed'], 2, final)
        states = {item['input_id']: item['state'] for item in final['items']}
        self.assertEqual(sorted(states.values()), ['failed', 'failed', 'no_tasks', 'no_tasks'])
        self.assertEqual(len(self.call('/recognition/batches')[1]), 2)
        time.sleep(.05)
        self.assertEqual(self.call('/sources/email/jobs/' + job['job_id'])[1], final,
                         'no worker may mutate the settled snapshot later')


    def test_provider_backoff_releases_capacity_and_reuses_captured_body(self):
        from server import model_queue
        import email_mailbox_pool
        calls = 0
        def limited_once(request, error):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise model_queue.EmailDeferred(.3, 'provider_backoff')
            return no_tasks(request, error)
        self.model.side_effect = limited_once
        original_fetch = email_source.fetch_selected
        with patch.object(email_source, 'fetch_selected', wraps=original_fetch) as fetch:
            job = self.start(self.ids(1), 'backoff-body-reuse')
            deadline = time.monotonic()+3
            released = False
            while time.monotonic() < deadline:
                snapshot = self.call('/sources/email/jobs/'+job['job_id'])[1]
                if snapshot.get('waiting_reason') == 'provider_backoff':
                    if email_scheduler.stats()['running'] == 0 and email_mailbox_pool.capacity_snapshot()['reserved_body_bytes'] == 0:
                        released = True
                        break
                time.sleep(.005)
            self.assertTrue(released, 'backoff held a dispatcher slot or body buffer')
            final = self.settled(job['job_id'])
            self.assertEqual(final['state'], 'succeeded', final)
            self.assertEqual(fetch.call_count, 1, 'backoff must not reread IMAP')
            self.assertEqual(calls, 2)

    def test_repeated_provider_429_has_bounded_retries_without_imap_reread(self):
        from server import model_queue
        self.model.side_effect = lambda *_args: (_ for _ in ()).throw(model_queue.EmailDeferred(.05, 'provider_backoff'))
        with patch.object(email_source,'fetch_selected',wraps=email_source.fetch_selected) as fetch:
            final = self.settled(self.start(self.ids(1),'bounded-rate-retry')['job_id'])
        self.assertEqual(final['state'],'failed',final)
        self.assertEqual(self.model.call_count,3)
        self.assertEqual(fetch.call_count,1)

    def test_ambiguous_timeout_is_not_automatically_replayed(self):
        self.model.side_effect = TimeoutError('synthetic ambiguous timeout')
        final = self.settled(self.start(self.ids(1),'no-timeout-replay')['job_id'])
        self.assertEqual(final['state'],'failed',final)
        self.assertEqual(self.model.call_count,1)

    def test_one_excluded_message_during_prepare_does_not_cancel_sibling(self):
        ids = self.ids(2)
        fetch = email_source.fetch_selected
        excluded = False
        def exclude_before_read(*args,**kwargs):
            nonlocal excluded
            if not excluded:
                excluded = True
                self.assertEqual(self.call('/sources/email/exclusions',dict(input_ids=[ids[0]],excluded=True))[0],200)
            return fetch(*args,**kwargs)
        with patch.object(email_source,'fetch_selected',side_effect=exclude_before_read):
            final = self.settled(self.start(ids,'exclude-during-prepare')['job_id'])
        self.assertEqual(final['state'],'succeeded',final)
        self.assertEqual(final['counts']['excluded'],1)
        self.assertEqual(final['counts']['processed'],1)
        self.assertEqual(final['counts']['failed'],0)
        self.assertEqual(self.model.call_count,1)
        self.assertEqual(self.model.call_args.args[0]['records'][0]['input_id'],ids[1])

    def test_stop_during_backoff_prevents_later_model_call(self):
        from server import model_queue
        self.model.side_effect = lambda *_args: (_ for _ in ()).throw(model_queue.EmailDeferred(1,'provider_backoff'))
        job = self.start(self.ids(1),'stop-backoff')
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            if self.call('/sources/email/jobs/'+job['job_id'])[1].get('waiting_reason') == 'provider_backoff':
                break
            time.sleep(.005)
        self.assertEqual(self.call('/sources/email/jobs/'+job['job_id']+'/stop',{})[0],200)
        final = self.settled(job['job_id'])
        self.assertEqual(final['state'],'stopped',final)
        self.assertEqual(self.model.call_count,1)


if __name__ == '__main__': unittest.main()
