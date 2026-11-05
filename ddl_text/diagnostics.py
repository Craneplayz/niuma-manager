"""Content-free events, including across disposable worker boundaries.

Never inspect exception messages, local variables, request bodies or model text.
The same sink is used by every producer; server persistence is a consumer.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
import math
import re
import time
import traceback

from contracts.recognition_errors import safe_schema_detail, DEFAULTS

_sink = ContextVar('recognition_events', default=None)
ROOT = Path(__file__).resolve().parents[1]
MAX_EVENTS = 200


def locations(error):
    result = []
    for frame in traceback.extract_tb(error.__traceback__)[-12:]:
        path = Path(frame.filename).resolve()
        if not path.is_relative_to(ROOT): continue
        relative = path.relative_to(ROOT).as_posix()
        if relative.split('/')[0] not in ('ddl_text', 'contracts', 'server'): continue
        result.append(dict(file=relative, line=frame.lineno, function=frame.name))
    return result


def sanitize(stage, fields):
    if not isinstance(stage, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', stage): return None
    safe = dict(stage=stage)
    for key in ('code', 'diagnostic', 'exception_type', 'mime_type', 'file_sha256', 'cache',
                'model', 'status', 'phase', 'boundary', 'skipped', 'policy_version', 'provider'):
        value = fields.get(key)
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}', value): safe[key] = value
    for key in ('seconds', 'timeout_seconds', 'bytes', 'page_start', 'page_count', 'total_pages',
                'completed_pages', 'native_pages', 'visual_pages', 'batch_pages', 'attempt',
                'record_index', 'http_status', 'input_tokens', 'output_tokens', 'total_tokens',
                'output_bytes', 'omitted_events', 'line', 'column'):
        value = fields.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value < 1e12: safe[key] = round(value, 3)
    if type(fields.get('exit_code')) is int and -(2**32) <= fields['exit_code'] < 2**32:
        safe['exit_code'] = fields['exit_code']
    detail = safe_schema_detail(fields.get('schema'))
    if detail: safe['schema'] = detail
    frames = []
    for frame in fields.get('locations', [])[:12] if isinstance(fields.get('locations'), list) else []:
        if not isinstance(frame, dict): continue
        file, line, function = frame.get('file'), frame.get('line'), frame.get('function')
        if (isinstance(file, str) and re.fullmatch(r'(ddl_text|contracts|server)/[A-Za-z0-9_/]+\.py', file)
                and type(line) is int and 0 < line < 100000
                and isinstance(function, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}|<lambda>|<module>', function)):
            frames.append(dict(file=file, line=line, function=function))
    if frames: safe['locations'] = frames
    return safe


def emit(stage, **fields):
    sink = _sink.get()
    if sink is None: return
    safe = sanitize(stage, fields)
    if safe is not None:
        try:
            stage = safe.pop('stage')
            sink(stage, **safe)
        except Exception:
            pass  # Diagnostics never change a recognition outcome.


def failure(error, *, stage='failure', reason=None, **fields):
    emit(stage, code=getattr(error, 'code', 'agent_unavailable'),
         diagnostic=reason or getattr(error, 'diagnostic', None) or DEFAULTS.get(getattr(error,'code',None), 'internal_error'),
         exception_type=type(error).__name__, schema=getattr(error, 'detail', None),
         locations=locations(error), **fields)


def forwarding_sink(send):
    """Bound worker IPC traffic while marking truncation and retaining failures."""
    sent, omitted = 0, 0
    def forward(stage, **fields):
        nonlocal sent, omitted
        if sent >= MAX_EVENTS and stage != 'failure':
            omitted += 1
            if omitted == 1: send(dict(stage='worker_events',status='truncated',omitted_events=1))
            return
        if stage == 'failure' and omitted:
            send(dict(stage='worker_events',status='truncated',omitted_events=omitted))
        sent += 1
        send(dict(stage=stage,**fields))
    return forward


@contextmanager
def scope(sink):
    token = _sink.set(sink)
    try: yield
    finally: _sink.reset(token)


def traced(function):
    @wraps(function)
    def run(*args, **kwargs):
        sink = kwargs.get('diagnostic_event') or _sink.get()
        with scope(sink): return function(*args, **kwargs)
    return run


@contextmanager
def phase(name, **fields):
    start = time.monotonic()
    emit(name, status='started', **fields)
    try:
        yield
    except Exception as error:
        failure(error, stage=name, status='failed', seconds=time.monotonic()-start)
        raise
    else:
        emit(name, status='succeeded', seconds=time.monotonic()-start)
