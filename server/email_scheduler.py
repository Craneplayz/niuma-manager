"""Bounded, per-account fair dispatch for email recognition work.

The queue stores only callable work (never message bodies). It submits at most
the global limit to the executor and gives each account at most its own limit.
"""
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import os
import socket
import sqlite3
import threading
import time
from pathlib import Path


def limits():
    def read(name, default, maximum):
        try:
            value = int(os.environ.get(name, default))
        except (TypeError, ValueError):
            value = int(default)
        return max(1, min(maximum, value))
    return read('NIUMA_EMAIL_GLOBAL_CONCURRENCY', '8', 32), read('NIUMA_EMAIL_CONCURRENCY', '2', 4)


class FairDispatcher:
    """Round-robin accounts while bounding both queued and running work."""
    def __init__(self, global_limit=8, account_limit=2):
        self.global_limit = global_limit
        self.account_limit = account_limit
        self.executor = ThreadPoolExecutor(max_workers=global_limit, thread_name_prefix='email-message')
        self.lock = threading.RLock()
        self.queues = {}
        self.rotation = deque()
        self.active = {}
        self.running = 0
        self.closed = False

    def submit(self, account, fn, *args, on_start=None, units=1):
        if type(units) is not int or not 1 <= units <= self.account_limit:
            raise ValueError('email dispatch units exceed per-account limit')
        result = Future()
        def wake_if_cancelled(future):
            if future.cancelled():
                with self.lock:
                    self._pump()
        result.add_done_callback(wake_if_cancelled)
        with self.lock:
            if self.closed:
                raise RuntimeError('email dispatcher is closed')
            if account not in self.queues:
                self.queues[account] = deque()
                self.rotation.append(account)
            self.queues[account].append((result, fn, args, on_start, units))
            self._pump()
        return result

    def _pump(self):
        if self.closed:
            return
        while self.running < self.global_limit and self.rotation:
            visited = 0
            account = None
            while visited < len(self.rotation):
                candidate = self.rotation[0]
                while self.queues[candidate] and self.queues[candidate][0][0].cancelled():
                    self.queues[candidate].popleft()
                if not self.queues[candidate]:
                    self.rotation.popleft()
                    del self.queues[candidate]
                    if not self.rotation:
                        return
                    continue
                head = self.queues[candidate][0] if self.queues[candidate] else None
                if (head and self.active.get(candidate, 0) + head[4] <= self.account_limit
                        and self.running + head[4] <= self.global_limit):
                    account = candidate
                    break
                self.rotation.rotate(-1)
                visited += 1
            if account is None:
                return
            queue = self.queues[account]
            result, fn, args, on_start, units = queue.popleft()
            if not result.set_running_or_notify_cancel():
                continue
            if queue:
                self.rotation.rotate(-1)
            else:
                self.rotation.remove(account)
                del self.queues[account]
            self.active[account] = self.active.get(account, 0) + units
            self.running += units
            try:
                def invoke(fn=fn, args=args, on_start=on_start):
                    if on_start:
                        on_start()
                    return fn(*args)
                inner = self.executor.submit(invoke)
            except BaseException as error:
                result.set_exception(error)
                self._released(account, units)
                continue

            def done(future, account=account, result=result, units=units):
                try:
                    try:
                        value = future.result()
                    except BaseException as error:
                        result.set_exception(error)
                    else:
                        result.set_result(value)
                finally:
                    with self.lock:
                        self._released(account, units)
            inner.add_done_callback(done)

    def _released(self, account, units):
        self.running -= units
        self.active[account] -= units
        if not self.active[account]:
            del self.active[account]
        self._pump()

    def stats(self):
        with self.lock:
            return {'running': self.running, 'queued': sum(map(len, self.queues.values())),
                    'accounts': len(self.queues), 'global_limit': self.global_limit,
                    'account_limit': self.account_limit}

    def shutdown(self, wait=True):
        with self.lock:
            self.closed = True
            pending = [future for queue in self.queues.values() for future, *_ in queue]
            self.queues.clear()
            self.rotation.clear()
        for future in pending:
            future.cancel()
        self.executor.shutdown(wait=wait, cancel_futures=True)


_instance = None
_instance_lock = threading.Lock()
_owner_threads = {}
_owned_paths = set()
_identity = (socket.gethostname(), os.getpid())


def _path_key(path):
    return os.path.realpath(str(path))


def _pid_alive(pid):
    """Treat permission failures as alive and only reclaim proven-dead PIDs."""
    if os.name == 'nt':
        # os.kill(pid, 0) terminates the target on Windows. A synchronize-only
        # handle can inspect liveness without requesting termination rights.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such PID
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0: exited
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        if getattr(error, 'errno', None) == 3:  # ESRCH
            return False
        return True
    return True


def _owner_reclaimable(row, host, now):
    if not row:
        return True
    if now - row[2] >= 30:
        return True
    return row[0] == host and row[1] != os.getpid() and not _pid_alive(row[1])


def _forget_owner(path, owner_thread=None):
    with _owner_lock:
        current = _owner_threads.get(path)
        if owner_thread is not None and current is not owner_thread:
            return
        _owned_paths.discard(path)
        _owner_threads.pop(path, None)


def register_process_owner(db, path):
    """Register ownership during application DB initialization transaction."""
    path = _path_key(path); host = socket.gethostname(); pid = os.getpid(); now = time.time()
    db.execute('''CREATE TABLE IF NOT EXISTS email_import_scheduler_owner(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), host TEXT NOT NULL,
        pid INTEGER NOT NULL, heartbeat REAL NOT NULL)''')
    row = db.execute('SELECT host,pid,heartbeat FROM email_import_scheduler_owner WHERE singleton=1').fetchone()
    if (row and (row[0], row[1]) != (host, pid)
            and not _owner_reclaimable(row, host, now)):
        return False
    db.execute('INSERT INTO email_import_scheduler_owner VALUES(1,?,?,?) '
        'ON CONFLICT(singleton) DO UPDATE SET host=excluded.host,pid=excluded.pid,heartbeat=excluded.heartbeat',
        (host, pid, now))
    _start_heartbeat(path, host, pid)
    return True
_owner_lock = threading.Lock()


def claim_process_owner(path):
    """Claim a renewable DB lease so only one app process dispatches each DB."""
    path = _path_key(path)
    host = socket.gethostname()
    pid = os.getpid()
    with _owner_lock:
        already_owner = path in _owned_paths
    if already_owner:
        try:
            check = sqlite3.connect(Path(path).as_uri() + '?mode=rw', uri=True, timeout=2)
            try:
                row = check.execute('SELECT host,pid FROM email_import_scheduler_owner WHERE singleton=1').fetchone()
            finally:
                check.close()
            if row == (host, pid):
                return True
        except sqlite3.Error:
            return False
        with _owner_lock:
            _owned_paths.discard(path)
            _owner_threads.pop(path, None)
    now = time.time()
    try:
        db = sqlite3.connect(Path(path).as_uri() + '?mode=rw', uri=True, timeout=10)
    except sqlite3.Error:
        return False
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS email_import_scheduler_owner(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), host TEXT NOT NULL,
            pid INTEGER NOT NULL, heartbeat REAL NOT NULL)''')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT host,pid,heartbeat FROM email_import_scheduler_owner WHERE singleton=1').fetchone()
        if (row and (row[0], row[1]) != (host, pid)
                and not _owner_reclaimable(row, host, now)):
            db.rollback()
            return False
        db.execute('INSERT INTO email_import_scheduler_owner VALUES(1,?,?,?) '
            'ON CONFLICT(singleton) DO UPDATE SET host=excluded.host,pid=excluded.pid,heartbeat=excluded.heartbeat',
            (host, pid, now))
        db.commit()
    finally:
        db.close()
    _start_heartbeat(path, host, pid)
    return True


def is_current_owner(db):
    row = db.execute('SELECT host,pid FROM email_import_scheduler_owner WHERE singleton=1').fetchone()
    return bool(row and (row[0], row[1]) == _identity)


def owns_process(path):
    with _owner_lock:
        return _path_key(path) in _owned_paths


def _start_heartbeat(path, host, pid):
    with _owner_lock:
        _owned_paths.add(path)
        if path not in _owner_threads:
            thread = threading.Thread(target=_heartbeat, args=(path, host, pid), daemon=True,
                                      name='email-scheduler-owner')
            _owner_threads[path] = thread
            thread.start()


def _heartbeat(path, host, pid):
    while True:
        time.sleep(5)
        if not os.path.exists(path):
            _forget_owner(path, threading.current_thread())
            return
        db = None
        try:
            db = sqlite3.connect(path, timeout=10)
            with db:
                cursor = db.execute('UPDATE email_import_scheduler_owner SET heartbeat=? WHERE singleton=1 AND host=? AND pid=?',
                           (time.time(), host, pid))
            db.close()
            db = None
            if cursor.rowcount == 0:
                _forget_owner(path, threading.current_thread())
                return
        except sqlite3.Error:
            if db is not None:
                db.close()
            if not os.path.exists(path):
                _forget_owner(path, threading.current_thread())
                return
            continue


def dispatcher():
    global _instance
    global_limit, account_limit = limits()
    with _instance_lock:
        if _instance is not None and (_instance.global_limit, _instance.account_limit) != (global_limit, account_limit):
            current = _instance.stats()
            if current['running'] == 0 and current['queued'] == 0:
                _instance.shutdown()
                _instance = None
        if _instance is None:
            _instance = FairDispatcher(global_limit, account_limit)
        return _instance


def stats():
    with _instance_lock:
        return _instance.stats() if _instance else {'running': 0, 'queued': 0, 'accounts': 0,
            'global_limit': limits()[0], 'account_limit': limits()[1]}


def capacity_snapshot():
    """Admin-safe live gauges; deliberately contains no user or account keys."""
    value = stats()
    return {'active': value['running'], 'queued': value['queued'],
            'global_limit': value['global_limit'], 'per_account_limit': value['account_limit']}
