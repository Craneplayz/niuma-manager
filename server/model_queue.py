"""One bounded FIFO admission queue for all business API model calls."""
from collections import deque
from contextlib import contextmanager
import threading
import time
import select
import socket
import os
import random

CONCURRENCY = 20
QUEUE_LIMIT = 100
QUEUE_SECONDS = 180
_request = threading.local()


class ModelQueue:
    def __init__(self, limit=CONCURRENCY, pending=QUEUE_LIMIT, wait=QUEUE_SECONDS):
        self.limit, self.pending, self.wait = limit, pending, wait
        self.condition = threading.Condition()
        self.active = 0
        self.waiters = deque()
        self.background = {}

    def acquire(self, blocking=True):
        with self.condition:
            self._prune_background()
            if disconnected():
                return False
            if self.active < self.limit and not self.waiters:
                self.active += 1
                return True
            if not blocking or len(self.waiters) >= self.pending:
                return False
            ticket = object()
            self.waiters.append(ticket)
            self.condition.notify_all()
            expires = time.monotonic() + self.wait
            try:
                while self.active >= self.limit or self.waiters[0] is not ticket:
                    remaining = expires - time.monotonic()
                    if remaining <= 0 or disconnected():
                        return False
                    self.condition.wait(min(remaining, .25))
                    self._prune_background()
                if time.monotonic() >= expires or disconnected():
                    return False
                self.active += 1
                return True
            finally:
                self.waiters.remove(ticket)
                self.condition.notify_all()

    def _prune_background(self):
        now = time.monotonic()
        for key,(ticket,expires) in tuple(self.background.items()):
            if expires <= now:
                self.background.pop(key,None)
                if ticket in self.waiters:
                    self.waiters.remove(ticket)
        self.condition.notify_all()

    def acquire_background(self, key):
        """Keep a FIFO ticket while the email worker releases its execution slot."""
        with self.condition:
            _request.background_wait_ms = 0
            self._prune_background()
            if disconnected():
                self._cancel_background_key(key)
                return False
            waiting = self.background.get(key)
            if waiting:
                ticket,expires = waiting
                if self.active < self.limit and self.waiters and self.waiters[0] is ticket:
                    self.waiters.popleft()
                    self.background.pop(key,None)
                    _request.background_wait_ms = max(0,(time.monotonic()-(expires-self.wait))*1000)
                    self.active += 1
                    self.condition.notify_all()
                    return True
                return False
            if self.active < self.limit and not self.waiters:
                self.active += 1
                return True
            if len(self.waiters) >= self.pending:
                return False
            ticket = object()
            self.background[key] = (ticket,time.monotonic()+self.wait)
            self.waiters.append(ticket)
            self.condition.notify_all()
            return False

    def _cancel_background_key(self,key):
        waiting = self.background.pop(key,None)
        if waiting and waiting[0] in self.waiters:
            self.waiters.remove(waiting[0])
        self.condition.notify_all()

    def cancel_background(self, prefix):
        with self.condition:
            for key in tuple(self.background):
                if key[:len(prefix)] == prefix:
                    self._cancel_background_key(key)

    def release(self):
        with self.condition:
            if not self.active:
                raise RuntimeError('model slot released without admission')
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


class EmailDeferred(Exception):
    """No new provider call is needed until this scheduling delay expires."""
    def __init__(self, wait_seconds=1, reason='model_capacity'):
        self.wait_seconds = max(.05, min(300, float(wait_seconds)))
        self.retry_after = self.wait_seconds
        self.reason = reason
        super().__init__(reason)


class ProviderBudget:
    """Process-local rolling quota reservations shared by business features.

    Reserve conservative token upper bounds, not claimed tokenizer counts.
    Unknown usage retains its reservation for the window; known usage can refund.
    Keys are endpoint/model identities, never credentials or account data.
    """
    def __init__(self, window=60, clock=time.monotonic):
        self.lock = threading.Lock()
        self.window, self.clock = window, clock
        self.entries = {}
        self.cooldowns = {}

    def reserve(self, key, requests=1, tokens=0):
        rpm = max(0, int(os.environ.get('NIUMA_MODEL_REQUESTS_PER_MINUTE', '0')))
        tpm = max(0, int(os.environ.get('NIUMA_MODEL_TOKENS_PER_MINUTE', '0')))
        now = self.clock()
        with self.lock:
            entries = self.entries.setdefault(key, deque())
            while entries and entries[0][0] <= now - self.window:
                entries.popleft()
            delay = max(0, self.cooldowns.get(key, 0) - now)
            if (rpm and requests > rpm) or (tpm and tokens > tpm):
                raise ValueError('provider budget cannot admit one operation')
            if ((rpm and sum(e[1] for e in entries) + requests > rpm) or
                    (tpm and sum(e[2] for e in entries) + tokens > tpm)):
                delay = max(delay, entries[0][0] + self.window - now if entries else self.window)
            if delay > 0:
                return None, delay
            receipt = [now, requests, tokens]
            if rpm or tpm:
                entries.append(receipt)
            return (key, receipt), 0

    def settle(self, receipt, requests=None, tokens=None):
        if not receipt:
            return
        with self.lock:
            entry = receipt[1]
            if type(requests) is int and requests >= 0:
                entry[1] = min(entry[1], requests)
            if type(tokens) is int and tokens >= 0:
                entry[2] = min(entry[2], tokens)

    def throttled(self, key, retry_after=None):
        # Only an explicit provider429 triggers automatic retry, never a timeout.
        delay = retry_after if type(retry_after) in (int, float) else random.uniform(1, 3)
        delay = max(.25, min(300, delay))
        with self.lock:
            self.cooldowns[key] = max(self.cooldowns.get(key, 0), self.clock() + delay)
        return delay

    def snapshot(self):
        with self.lock:
            now = self.clock()
            return dict(coolingProviders=sum(end > now for end in self.cooldowns.values()),
                        requestsPerMinute=max(0, int(os.environ.get('NIUMA_MODEL_REQUESTS_PER_MINUTE', '0'))),
                        tokensPerMinute=max(0, int(os.environ.get('NIUMA_MODEL_TOKENS_PER_MINUTE', '0'))))


PROVIDER = ProviderBudget()


def provider_reservation(options, request, output_tokens=8192, attempts=1, instructions=None, fallback_tokens=None):
    """Admission receipt and retry delay; UTF8 bytes conservatively bound input tokens."""
    import json
    key = (options.get('endpoint', ''), options.get('model', ''))
    if not int(os.environ.get('NIUMA_MODEL_REQUESTS_PER_MINUTE', '0')) and not int(os.environ.get('NIUMA_MODEL_TOKENS_PER_MINUTE', '0')):
        receipt, delay = PROVIDER.reserve(key, 1, 0)
        return key, receipt, delay
    count = len(request.get('records', [])) or 1
    from ddl_text.extraction import load_instructions
    from ddl_text.draft_schema import draft_schema
    from ddl_text.contracts import normalize_record
    if instructions is None:
        input_bound = 0
        for raw in request.get('records', []):
            record = normalize_record(raw, request.get('contract_version', '0.1'))
            prompt = (load_instructions(record) +
                      "\nReturn only a JSON object matching this schema:\n" + json.dumps(draft_schema(record)))
            input_bound += len(prompt.encode('utf-8')) + len(json.dumps(record, ensure_ascii=False).encode('utf-8'))
    else:
        input_bound = len(instructions.encode('utf-8')) + len(json.dumps(request, ensure_ascii=False).encode('utf-8'))
    # Include framing overhead; UTF8 bytes are deliberately conservative, not token usage.
    input_bound += 256 * count
    output_bound = count * (output_tokens + (fallback_tokens or 0)) if fallback_tokens else attempts * count * output_tokens
    receipt, delay = PROVIDER.reserve(key, count * attempts, attempts * input_bound + output_bound)
    return key, receipt, delay


def settle_provider_usage(receipt, metrics):
    total = metrics.get('total_tokens') if isinstance(metrics, dict) else None
    if type(total) is int and total >= 0:
        PROVIDER.settle(receipt, requests=1, tokens=total)


def provider_observer(options, receipt):
    key = (options.get('endpoint', ''), options.get('model', ''))
    def observe(metrics):
        if metrics.get('provider_status') == 429:
            PROVIDER.throttled(key, metrics.get('retry_after_seconds'))
        else:
            settle_provider_usage(receipt, metrics)
    return observe


@contextmanager
def background(identity):
    previous = getattr(_request,'background_identity',None)
    _request.background_identity = identity
    try:
        yield
    finally:
        _request.background_identity = previous


def background_identity():
    return getattr(_request,'background_identity',None)


def background_wait_ms():
    return getattr(_request,'background_wait_ms',0)


def capacity_snapshot():
    with ACTIVE.condition:
        return dict(active=ACTIVE.active,queued=len(ACTIVE.waiters),limit=ACTIVE.limit)
