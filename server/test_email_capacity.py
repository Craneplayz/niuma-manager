"""Offline resource bounds, write admission and shared provider capacity."""
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import email_db_gate
import email_mailbox_pool as pool
import email_telemetry
import manage_environment as manager
from server import model_queue
from ddl_text.adapters import AgentError
from ddl_text.chat_client import ChatClient
from ddl_text.config import Settings

class MailboxPoolTests(unittest.TestCase):
    def test_global_session_limit_bounds_distinct_mailboxes(self):
        old_global = pool._global
        pool._global = threading.BoundedSemaphore(1)
        entered = threading.Event()
        release = threading.Event()
        other = threading.Event()
        try:
            def owner():
                with pool.mailbox('a@pku.edu.cn'):
                    entered.set()
                    release.wait(2)
            def waiter():
                with pool.mailbox('b@pku.edu.cn'):
                    other.set()
            one = threading.Thread(target=owner); one.start()
            self.assertTrue(entered.wait(1))
            two = threading.Thread(target=waiter); two.start()
            time.sleep(.05)
            self.assertFalse(other.is_set())
            release.set(); one.join(2); two.join(2)
            self.assertTrue(other.is_set())
        finally:
            pool._global = old_global

    def test_same_mailbox_is_exclusive_and_different_mailboxes_can_enter(self):
        first = threading.Event()
        release = threading.Event()
        second_entered = threading.Event()

        def owner():
            with pool.mailbox('a@pku.edu.cn'):
                self.assertEqual(pool.capacity_snapshot()['active_sessions'], 1)
                first.set()
                release.wait(2)

        def waiter():
            with pool.mailbox('A@PKU.EDU.CN'):
                second_entered.set()

        one = threading.Thread(target=owner)
        one.start()
        self.assertTrue(first.wait(1))
        two = threading.Thread(target=waiter)
        two.start()
        time.sleep(.05)
        self.assertFalse(second_entered.is_set())
        with pool.mailbox('b@pku.edu.cn'):
            self.assertFalse(second_entered.is_set())
        release.set()
        one.join(2); two.join(2)
        self.assertTrue(second_entered.is_set())

    def test_wait_is_cancellation_aware(self):
        entered = threading.Event()
        cancelled = threading.Event()

        class Stopped(Exception):
            pass

        def owner():
            with pool.mailbox('a@pku.edu.cn'):
                entered.set()
                cancelled.wait(2)

        thread = threading.Thread(target=owner)
        thread.start()
        self.assertTrue(entered.wait(1))
        checks = {'count': 0}

        def check():
            checks['count'] += 1
            if checks['count'] >= 2:
                raise Stopped()

        with self.assertRaises(Stopped):
            with pool.mailbox('a@pku.edu.cn', cancel_check=check):
                self.fail('cancelled waiter entered the mailbox')
        cancelled.set(); thread.join(2)
        self.assertGreaterEqual(checks['count'], 2)

    def test_body_reservations_enforce_byte_cap(self):
        first = threading.Event()
        release = threading.Event()
        second = threading.Event()
        old_bytes = pool.BODY_BYTES
        pool.BODY_BYTES = 262144
        try:
            def owner():
                with pool.body_slot(196608):
                    snapshot = pool.capacity_snapshot()
                    self.assertEqual(snapshot['reserved_body_bytes'], 196608)
                    self.assertEqual(snapshot['active_body_buffers'], 1)
                    first.set()
                    release.wait(2)
            def waiter():
                with pool.body_slot(131072):
                    second.set()
            one = threading.Thread(target=owner)
            one.start()
            self.assertTrue(first.wait(1))
            two = threading.Thread(target=waiter)
            two.start()
            time.sleep(.05)
            self.assertFalse(second.is_set())
            release.set(); one.join(2); two.join(2)
            self.assertTrue(second.is_set())
        finally:
            pool.BODY_BYTES = old_bytes

    def test_batch_body_reservation_counts_each_prefetched_message(self):
        old_limit = pool.BODY_PREFETCH
        pool.BODY_PREFETCH = 2
        first = threading.Event(); release = threading.Event(); batch = threading.Event()
        try:
            def one_body():
                with pool.body_buffer(131072, count=1):
                    first.set(); release.wait(2)
            def two_bodies():
                with pool.body_buffer(262144, count=2):
                    batch.set()
            a = threading.Thread(target=one_body); a.start()
            self.assertTrue(first.wait(1))
            b = threading.Thread(target=two_bodies); b.start()
            time.sleep(.05)
            self.assertFalse(batch.is_set())
            release.set(); a.join(2); b.join(2)
            self.assertTrue(batch.is_set())
        finally:
            pool.BODY_PREFETCH = old_limit

    def test_body_wait_is_cancellation_aware_and_releases_accounting(self):
        old_limit = pool.BODY_PREFETCH
        pool.BODY_PREFETCH = 1
        entered = threading.Event(); release = threading.Event()
        class Stopped(Exception):
            pass
        try:
            def owner():
                with pool.body_buffer(131072):
                    entered.set(); release.wait(2)
            thread = threading.Thread(target=owner); thread.start()
            self.assertTrue(entered.wait(1))
            calls = {'count': 0}
            def cancel():
                calls['count'] += 1
                if calls['count'] >= 2:
                    raise Stopped()
            with self.assertRaises(Stopped):
                with pool.body_buffer(131072, cancel_check=cancel):
                    self.fail('cancelled body waiter entered the reservation')
            release.set(); thread.join(2)
            self.assertEqual(pool.capacity_snapshot()['waiting_body_buffers'], 0)
        finally:
            pool.BODY_PREFETCH = old_limit


class WriteAdmissionTests(unittest.TestCase):
    def test_user_control_overtakes_waiting_progress_without_losing_progress(self):
        gate = email_db_gate.WriteGate()
        order = []
        def worker(urgent,label):
            with gate.enter(urgent=urgent): order.append(label)
        with gate.enter():
            normal = threading.Thread(target=worker,args=(False,'progress'))
            normal.start()
            with gate.condition:
                self.assertTrue(gate.condition.wait_for(lambda:bool(gate.normal),timeout=.2))
            urgent = threading.Thread(target=worker,args=(True,'control'))
            urgent.start()
            deadline = time.monotonic()+1
            while not gate.urgent and time.monotonic()<deadline: time.sleep(.001)
        normal.join(2); urgent.join(2)
        self.assertEqual(order,['control','progress'])

    def test_path_gate_cleans_up_after_exception(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'synthetic.sqlite3'
            with self.assertRaises(ValueError):
                with email_db_gate.write(path): raise ValueError('synthetic')
            self.assertFalse(email_db_gate._gates)
            with email_db_gate.write(path,urgent=True): pass
            self.assertFalse(email_db_gate._gates)


class CapacityControlsTests(unittest.TestCase):
    def test_missing_fallback_usage_does_not_refund_its_quota(self):
        import recognition
        from ddl_text.tests.test_pipeline import example
        request = example('no-task')['input']
        for raw in request['records']:
            raw['platform'] = 'email'
        options = dict(provider='qiniu',model='synthetic',endpoint='https://synthetic.invalid',key='synthetic-only')
        for fallback_timeout in (False, True):
            with self.subTest(fallback_timeout=fallback_timeout):
                gate = model_queue.ProviderBudget()
                receipt, _ = gate.reserve('synthetic', 2, 100)
                def process(request, adapter, settings, **kwargs):
                    self.assertTrue(kwargs['attempt_guard']())
                    kwargs['attempt_observer'](dict(generation_started=True,generation_ok=True,
                        generation_ms=1,total_tokens=10,finish_reason='length' if fallback_timeout else 'stop'))
                    if fallback_timeout:
                        self.assertTrue(kwargs['attempt_guard']())
                        # A killed worker never delivered telemetry for this provider call.
                        return dict(status='failed',errors=[dict(code='agent_timeout')])
                    return dict(status='no_tasks')
                with patch.dict(os.environ, TEXT_HARNESS_URL=''), \
                     patch.object(recognition,'_ACTIVE',model_queue.ModelQueue(limit=1)), \
                     patch.object(model_queue,'PROVIDER',gate), \
                     patch.object(model_queue,'provider_reservation',return_value=('synthetic',receipt,0)), \
                     patch.object(recognition,'live_options',return_value=options), \
                     patch.object(recognition,'make_client'), \
                     patch.object(recognition,'text_policy_manifest',return_value=dict(components=recognition._POLICY_COMPONENTS)), \
                     patch.object(recognition,'process_request',side_effect=process):
                    recognition.recognize(request, RuntimeError)
                self.assertEqual(receipt[1][1:], [2,100] if fallback_timeout else [1,10])

    def test_quota_window_refund_and_provider_isolation(self):
        now = [0.0]
        gate = model_queue.ProviderBudget(window=60, clock=lambda: now[0])
        with patch.dict(os.environ, NIUMA_MODEL_REQUESTS_PER_MINUTE='2', NIUMA_MODEL_TOKENS_PER_MINUTE='100'):
            receipt, delay = gate.reserve('synthetic', 2, 100)
            self.assertEqual(delay, 0)
            self.assertEqual(gate.reserve('synthetic', 1, 1)[1], 60)
            gate.settle(receipt, requests=1, tokens=40)
            self.assertEqual(gate.reserve('synthetic', 1, 60)[1], 0)
            self.assertEqual(gate.reserve('different-provider', 1, 1)[1], 0)
            now[0] = 61
            self.assertEqual(gate.reserve('synthetic', 2, 100)[1], 0)
            with self.assertRaises(ValueError):
                gate.reserve('synthetic', 3, 1)

    def test_429_cooldown_applies_even_with_unconfigured_quota(self):
        now = [100.0]
        gate = model_queue.ProviderBudget(clock=lambda: now[0])
        with patch.dict(os.environ, NIUMA_MODEL_REQUESTS_PER_MINUTE='0', NIUMA_MODEL_TOKENS_PER_MINUTE='0'):
            self.assertEqual(gate.throttled('provider', 5), 5)
            self.assertEqual(gate.reserve('provider')[1], 5)
            now[0] += 6
            self.assertEqual(gate.reserve('provider')[1], 0)

    def test_capacity_profiles_default_and_cannot_inherit_shell_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = manager.initialize('test', Path(folder))
            config = manager.defaults('test')
            for key in manager.CAPACITY_FIELDS:
                config.pop(key)
            (folder/'config.json').write_text(json.dumps(config))
            _, loaded = manager.read_config('test', folder.parent)
            with patch.dict(os.environ, NIUMA_EMAIL_GLOBAL_CONCURRENCY='999', NIUMA_MODEL_REQUESTS_PER_MINUTE='999'):
                child = manager.local_environment('test', folder, loaded)
            self.assertEqual(child['NIUMA_EMAIL_GLOBAL_CONCURRENCY'], '8')
            self.assertEqual(child['NIUMA_MODEL_REQUESTS_PER_MINUTE'], '0')
            for key in manager.CAPACITY_FIELDS:
                config = dict(loaded, **{key: True})
                (folder/'config.json').write_text(json.dumps(config))
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                    manager.read_config('test', folder.parent)

    def test_429_transport_keeps_only_safe_header_metrics(self):
        error = HTTPError('https://synthetic.invalid', 429, 'PRIVATE_SENTINEL',
                          {'Retry-After': '5'}, io.BytesIO(b'PRIVATE_SENTINEL'))
        client = ChatClient('synthetic-only')
        with patch('ddl_text.chat_client.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(AgentError):
                client.complete_json('synthetic', '{}', Settings(mode='real', model='synthetic', endpoint='https://synthetic.invalid'))
        self.assertEqual(client.response_metrics, dict(provider_status=429, retry_after_seconds=5.0))
        self.assertNotIn('PRIVATE_SENTINEL', repr(client.response_metrics))

    def test_email_model_capacity_defers_without_starting_provider(self):
        import recognition
        from ddl_text.tests.test_pipeline import example
        request = example('no-task')['input']
        request['records'][0]['platform'] = 'email'
        queue = model_queue.ModelQueue(limit=1)
        self.assertTrue(queue.acquire())
        with model_queue.background(('synthetic-job','mail')), patch.object(recognition, '_ACTIVE', queue), patch.object(recognition, 'make_client') as client:
            with self.assertRaises(model_queue.EmailDeferred) as caught:
                recognition.recognize(request, RuntimeError)
        self.assertEqual(caught.exception.reason, 'model_capacity')
        client.assert_not_called()
        self.assertEqual(queue.active, 1)
        queue.release()

    def test_explicit_email_429_defers_and_releases_shared_slot(self):
        import recognition
        from ddl_text.tests.test_pipeline import example
        request = example('no-task')['input']
        request['records'][0]['platform'] = 'email'
        queue = model_queue.ModelQueue(limit=1)
        options = dict(provider='qiniu',model='synthetic',endpoint='https://synthetic.invalid',key='synthetic-only')
        def limited(request, adapter, settings, **kwargs):
            kwargs['attempt_observer'](dict(generation_started=True,generation_ok=False,generation_ms=1,provider_status=429,retry_after_seconds=2))
            return dict(status='failed')
        with model_queue.background(('synthetic-job','mail')), patch.dict(os.environ, TEXT_HARNESS_URL='', NIUMA_MODEL_REQUESTS_PER_MINUTE='0', NIUMA_MODEL_TOKENS_PER_MINUTE='0'), patch.object(recognition,'_ACTIVE',queue), patch.object(model_queue,'PROVIDER',model_queue.ProviderBudget()), patch.object(recognition,'live_options',return_value=options), patch.object(recognition,'make_client'), patch.object(recognition,'text_policy_manifest',return_value=dict(components=recognition._POLICY_COMPONENTS)), patch.object(recognition,'process_request',side_effect=limited):
            with self.assertRaises(model_queue.EmailDeferred) as caught:
                recognition.recognize(request, RuntimeError)
            self.assertEqual(caught.exception.reason, 'provider_backoff')
            self.assertEqual(caught.exception.wait_seconds, 2)
            self.assertEqual(queue.active, 0)

    def test_429_observer_shares_cooldown_with_other_features(self):
        gate = model_queue.ProviderBudget()
        options = dict(endpoint='https://synthetic.invalid',model='synthetic')
        with patch.object(model_queue,'PROVIDER',gate), patch.dict(os.environ,NIUMA_MODEL_REQUESTS_PER_MINUTE='0',NIUMA_MODEL_TOKENS_PER_MINUTE='0'):
            callback = model_queue.provider_observer(options, None)
            callback(dict(provider_status=429,retry_after_seconds=5))
            self.assertGreater(gate.reserve((options['endpoint'],options['model']))[1], 4)

    def test_status_metrics_bounded_and_content_free(self):
        for _ in range(1100):
            email_telemetry.observe_status(5, 200)
        metrics = email_telemetry.capacity_snapshot()
        self.assertEqual(metrics['statusSamples'], 1024)
        self.assertEqual(metrics['statusP95Ms'], 5)
        self.assertEqual(metrics['statusMeanBytes'], 200)
        self.assertEqual(metrics['scope'], 'current_process')


if __name__ == "__main__":
    unittest.main()
