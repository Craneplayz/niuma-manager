"""Short per-database write admission, prioritizing user controls over progress.

SQLite remains the cross-process transaction authority. This process-local gate
prevents background progress writers from starving a burst of user submissions.
"""
from collections import deque
from contextlib import contextmanager
from pathlib import Path
import threading
import time


class WriteGate:
    def __init__(self):
        self.condition = threading.Condition()
        self.urgent = deque()
        self.normal = deque()
        self.active = False
        self.burst = 0

    @contextmanager
    def enter(self, urgent=False, timeout=20):
        ticket = object()
        queue = self.urgent if urgent else self.normal
        deadline = time.monotonic()+timeout
        with self.condition:
            queue.append(ticket)
            try:
                while True:
                    preferred = self.urgent if self.urgent and (self.burst < 8 or not self.normal) else self.normal
                    if not self.active and preferred and preferred[0] is ticket:
                        queue.popleft()
                        self.active = True
                        self.burst = self.burst+1 if urgent else 0
                        break
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('email database admission timeout')
                    self.condition.wait(remaining)
            except BaseException:
                if ticket in queue: queue.remove(ticket)
                self.condition.notify_all()
                raise
        try:
            yield
        finally:
            with self.condition:
                self.active = False
                self.condition.notify_all()


_lock = threading.Lock()
_gates = {}


@contextmanager
def write(path, urgent=False):
    key = str(Path(path).resolve())
    with _lock:
        gate, references = _gates.get(key, (WriteGate(),0))
        _gates[key] = (gate,references+1)
    try:
        with gate.enter(urgent=urgent):
            yield
    finally:
        with _lock:
            current, references = _gates[key]
            if references == 1:
                del _gates[key]
            else:
                _gates[key] = (current,references-1)
