import socket
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import recognition
import planning
import task_decomposition
from server import model_queue


class ModelQueueTests(unittest.TestCase):
    def test_root_package_imports_share_the_same_model_queue(self):
        root = Path(__file__).resolve().parent.parent
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        script = '''import sys
from server import text_contract
sys.modules['text_contract'] = text_contract
from server import recognition, planning, task_decomposition, model_queue
assert recognition._ACTIVE is planning._ACTIVE is task_decomposition._ACTIVE is model_queue.ACTIVE
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=root, env=env,
            capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def queued(self, queue, count):
        with queue.condition:
            self.assertTrue(queue.condition.wait_for(lambda: len(queue.waiters)==count, timeout=3))

    def test_all_features_share_the_twenty_slot_pool(self):
        self.assertIs(recognition._ACTIVE, model_queue.ACTIVE)
        self.assertIs(planning._ACTIVE, model_queue.ACTIVE)
        self.assertIs(task_decomposition._ACTIVE, model_queue.ACTIVE)
        self.assertEqual(model_queue.ACTIVE.limit,20)
        queue=model_queue.ModelQueue()
        release, entered=threading.Event(),threading.Event()
        lock=threading.Lock()
        running=peak=0
        def work():
            nonlocal running,peak
            self.assertTrue(queue.acquire())
            try:
                with lock:
                    running+=1;peak=max(peak,running)
                    if running==20:entered.set()
                self.assertTrue(release.wait(5))
                with lock:running-=1
            finally:queue.release()
        with ThreadPoolExecutor(max_workers=25) as pool:
            first=[pool.submit(work) for _ in range(20)]
            waiting=[]
            try:
                self.assertTrue(entered.wait(3))
                waiting=[pool.submit(work) for _ in range(5)]
                self.queued(queue,5)
                self.assertEqual(queue.active,20)
            finally:release.set()
            for future in first+waiting:future.result(timeout=3)
        self.assertEqual(peak,20)
        self.assertEqual(queue.active,0)
        self.assertEqual(len(queue.waiters),0)

    def test_fifo_and_no_nonblocking_jump_ahead(self):
        queue=model_queue.ModelQueue(limit=1)
        self.assertTrue(queue.acquire())
        order=[]
        def work(index):
            self.assertTrue(queue.acquire())
            try:order.append(index)
            finally:queue.release()
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures=[]
            for index in range(3):
                futures.append(pool.submit(work,index));self.queued(queue,index+1)
            self.assertFalse(queue.acquire(blocking=False))
            queue.release()
            for future in futures:future.result(timeout=3)
        self.assertEqual(order,[0,1,2])

    def test_queue_is_bounded_and_timeout_removes_ticket(self):
        queue=model_queue.ModelQueue(limit=1,pending=1,wait=.05)
        self.assertTrue(queue.acquire())
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting=pool.submit(queue.acquire)
            self.queued(queue,1)
            self.assertFalse(queue.acquire())
            self.assertFalse(waiting.result(timeout=3))
        self.assertEqual(len(queue.waiters),0)
        queue.release()
        self.assertTrue(queue.acquire());queue.release()

    def test_exception_releases_slot(self):
        queue=model_queue.ModelQueue(limit=1)
        with patch.object(model_queue,'ACTIVE',queue):
            with self.assertRaisesRegex(RuntimeError,'synthetic'):
                with model_queue.slot(lambda status,message:RuntimeError(message)):
                    raise RuntimeError('synthetic')
        self.assertEqual(queue.active,0)

    def test_disconnected_waiter_never_starts_model(self):
        queue=model_queue.ModelQueue(limit=1)
        self.assertTrue(queue.acquire())
        server,client=socket.socketpair()
        def wait():
            with model_queue.request(server):return queue.acquire()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(wait);self.queued(queue,1)
                client.close()
                self.assertFalse(future.result(timeout=2))
            self.assertEqual(len(queue.waiters),0)
        finally:
            client.close();server.close();queue.release()

    def test_stopped_background_waiter_never_starts_model(self):
        queue=model_queue.ModelQueue(limit=1)
        self.assertTrue(queue.acquire())
        stop=threading.Event()
        def wait():
            with model_queue.cancel_if(stop.is_set):return queue.acquire()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(wait);self.queued(queue,1)
                stop.set()
                self.assertFalse(future.result(timeout=2))
        finally:queue.release()

    def test_already_cancelled_request_does_not_consume_a_free_slot(self):
        queue=model_queue.ModelQueue()
        with model_queue.cancel_if(lambda:True):self.assertFalse(queue.acquire())
        self.assertEqual(queue.active,0)
        self.assertTrue(queue.acquire());queue.release()


    def test_background_ticket_retains_fifo_without_holding_worker(self):
        queue = model_queue.ModelQueue(limit=1)
        self.assertTrue(queue.acquire())
        self.assertFalse(queue.acquire_background(('email',)))
        entered = threading.Event()
        def interactive():
            self.assertTrue(queue.acquire())
            entered.set(); queue.release()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(interactive)
            self.queued(queue,2)
            queue.release()
            self.assertFalse(entered.is_set())
            self.assertTrue(queue.acquire_background(('email',)))
            queue.release()
            future.result(timeout=3)
        self.assertTrue(entered.is_set())
        self.assertFalse(queue.background)

    def test_background_cancellation_and_expiry_unblock_interactive_waiters(self):
        queue = model_queue.ModelQueue(limit=1,wait=.05)
        self.assertTrue(queue.acquire())
        self.assertFalse(queue.acquire_background(('job','email')))
        queue.cancel_background(('job',))
        self.assertFalse(queue.waiters)
        self.assertFalse(queue.background)
        self.assertFalse(queue.acquire_background(('other','email')))
        queue.release()
        time.sleep(.06)
        self.assertTrue(queue.acquire(blocking=False))
        queue.release()
        self.assertFalse(queue.background)


if __name__=='__main__':unittest.main()
