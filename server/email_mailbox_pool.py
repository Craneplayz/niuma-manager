"""Process-wide bounds for short-lived mailbox and body fetch work."""
import os
import hashlib
import threading
import time
from contextlib import contextmanager


class BodyCapacityTimeout(TimeoutError):
    """Only local body-buffer admission failed; no provider call was made."""


def _setting(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = int(default)
    return min(maximum, max(minimum, value))


IMAP_CONCURRENCY = _setting('NIUMA_EMAIL_IMAP_CONCURRENCY', 4, 1, 16)
BODY_PREFETCH = _setting('NIUMA_EMAIL_PREFETCH', 16, 1, 128)
BODY_BYTES = _setting('NIUMA_EMAIL_PREFETCH_BYTES', 2 * 1024 * 1024, 131072, 32 * 1024 * 1024)
WAIT_SECONDS = 80
_global = threading.BoundedSemaphore(IMAP_CONCURRENCY)
_state = threading.Condition()
_mailbox_locks = [threading.Lock() for _ in range(256)]
_reserved_body_bytes = 0
_active_sessions = 0
_waiting_sessions = 0
_active_body_buffers = 0
_waiting_body_buffers = 0
_active_body_bodies = 0


def capacity_snapshot():
    """Return aggregate operational counts without mailbox identifiers."""
    with _state:
        return {
            'active_sessions': _active_sessions,
            'waiting_sessions': _waiting_sessions,
            'session_limit': IMAP_CONCURRENCY,
            'active_body_buffers': _active_body_buffers,
            'active_body_bodies': _active_body_bodies,
            'waiting_body_buffers': _waiting_body_buffers,
            'body_buffer_limit': BODY_PREFETCH,
            'reserved_body_bytes': _reserved_body_bytes,
            'body_byte_limit': BODY_BYTES,
        }


def _cancel(check):
    if check is not None:
        check()


def _acquire(semaphore, check, deadline):
    while True:
        _cancel(check)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('mailbox capacity wait expired')
        if semaphore.acquire(timeout=min(0.1, remaining)):
            try:
                _cancel(check)
            except BaseException:
                semaphore.release()
                raise
            return


@contextmanager
def mailbox(address, cancel_check=None):
    """Limit active IMAP sessions globally and to one per normalized mailbox."""
    key = str(address).strip().lower()
    slot = int.from_bytes(hashlib.sha256(key.encode('utf-8')).digest()[:2], 'big') % len(_mailbox_locks)
    lock = _mailbox_locks[slot]
    deadline = time.monotonic() + WAIT_SECONDS
    global _active_sessions, _waiting_sessions
    with _state:
        _waiting_sessions += 1
    waiting_counted = True
    lock_acquired = False
    global_acquired = False
    active_counted = False
    try:
        _acquire(lock, cancel_check, deadline)
        lock_acquired = True
        try:
            _acquire(_global, cancel_check, deadline)
            global_acquired = True
            with _state:
                _active_sessions += 1
                _waiting_sessions -= 1
                waiting_counted = False
                active_counted = True
            yield
        finally:
            if active_counted:
                with _state:
                    _active_sessions -= 1
            if global_acquired:
                _global.release()
    finally:
        if waiting_counted:
            with _state:
                _waiting_sessions -= 1
        if lock_acquired:
            lock.release()


@contextmanager
def body_slot(max_bytes, cancel_check=None, *, count=1, timeout=None):
    """Reserve body count and literal bytes through consumption; object overhead is extra."""
    global _reserved_body_bytes, _active_body_buffers, _waiting_body_buffers, _active_body_bodies
    if type(max_bytes) is not int or max_bytes < 1 or max_bytes > BODY_BYTES:
        raise ValueError('invalid body reservation')
    if type(count) is not int or count < 1 or count > BODY_PREFETCH:
        raise ValueError('invalid body count reservation')
    deadline = time.monotonic() + (WAIT_SECONDS if timeout is None else timeout)
    with _state:
        _waiting_body_buffers += 1
    reserved = False
    try:
        with _state:
            while (_reserved_body_bytes + max_bytes > BODY_BYTES or
                   _active_body_bodies + count > BODY_PREFETCH):
                _cancel(cancel_check)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BodyCapacityTimeout('body capacity wait expired')
                _state.wait(min(0.1, remaining))
            _cancel(cancel_check)
            _reserved_body_bytes += max_bytes
            _active_body_buffers += 1
            _active_body_bodies += count
            _waiting_body_buffers -= 1
            reserved = True
        yield
    finally:
        if reserved:
            with _state:
                _reserved_body_bytes -= max_bytes
                _active_body_buffers -= 1
                _active_body_bodies -= count
                _state.notify_all()
        else:
            with _state:
                _waiting_body_buffers -= 1


# The caller may keep the reservation around after FETCH returns, until the
# recognition worker has consumed or discarded every body in its batch.
body_buffer = body_slot
