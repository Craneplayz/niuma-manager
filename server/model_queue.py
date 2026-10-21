"""One bounded FIFO admission queue for all business API model calls."""
from collections import deque
from contextlib import contextmanager
import threading
import time
import select
import socket
from server import operations

CONCURRENCY = 20
QUEUE_LIMIT = 100
QUEUE_SECONDS = 180
_request = threading.local()


class ModelQueue:
    def __init__(self, limit=CONCURRENCY, pending=QUEUE_LIMIT, wait=QUEUE_SECONDS, name='model_queue'):
        self.limit, self.pending, self.wait = limit, pending, wait
        self.condition = threading.Condition()
        self.active = 0
        self.waiters = deque()
        self.waiting_at = {}
        self.name = name
        self._local = threading.local()

    @property
    def failure(self):
        return getattr(self._local, 'failure', None)

    def acquire(self, blocking=True):
        start=time.monotonic()
        acquired=self._acquire(blocking)
        operations.emit(self.name,(time.monotonic()-start)*1000,result='ok' if acquired else 'failed',code='ok' if acquired else 'queue_unavailable')
        return acquired

    def snapshot(self):
        with self.condition:
            return dict(running=self.active,queued=len(self.waiters),oldestWaitSeconds=round(max(0,time.monotonic()-min(self.waiting_at.values())),1) if self.waiting_at else 0)

    def _acquire(self, blocking=True):
        self._local.failure = None
        # Cancellation may perform SQLite I/O; never hold the condition while probing.
        if disconnected():
            self._local.failure = 'stopped'
            return False
        with self.condition:
            if self.active < self.limit and not self.waiters:
                self.active += 1
                return True
            if not blocking or len(self.waiters) >= self.pending:
                self._local.failure = 'queue_full'
                return False
            ticket = object()
            self.waiters.append(ticket)
            self.waiting_at[ticket]=time.monotonic()
            self.condition.notify_all()
            expires = time.monotonic() + self.wait
        try:
            while True:
                if disconnected():
                    self._local.failure = 'stopped'
                    return False
                with self.condition:
                    remaining = expires - time.monotonic()
                    if remaining <= 0:
                        self._local.failure = 'queue_timeout'
                        return False
                    if self.active < self.limit and self.waiters[0] is ticket:
                        self.active += 1
                        return True
                    self.condition.wait(min(remaining, .25))
        finally:
            with self.condition:
                self.waiters.remove(ticket)
                self.waiting_at.pop(ticket,None)
                self.condition.notify_all()

    def release(self):
        with self.condition:
            if not self.active:
                raise RuntimeError('queue slot released without admission')
            self.active -= 1
            self.condition.notify_all()


ACTIVE = ModelQueue()
WAIT_ERROR = '模型请求排队已满或等待超时，请稍后重试；未开始模型调用'


def disconnected():
    cancelled = getattr(_request, 'cancelled', None)
    if cancelled and cancelled():
        return True
    connection = getattr(_request, 'connection', None)
    if connection is None:
        return False
    try:
        ready, _, _ = select.select([connection], [], [], 0)
        return bool(ready) and connection.recv(1, socket.MSG_PEEK) == b''
    except (OSError, ValueError):
        return True


@contextmanager
def cancel_if(callback):
    previous = getattr(_request, 'cancelled', None)
    _request.cancelled = callback
    try:
        yield
    finally:
        _request.cancelled = previous


@contextmanager
def request(connection):
    _request.connection = connection
    try:
        yield
    finally:
        del _request.connection


@contextmanager
def slot(error):
    if not ACTIVE.acquire():
        raise error(503, WAIT_ERROR)
    try:
        yield
    finally:
        ACTIVE.release()
