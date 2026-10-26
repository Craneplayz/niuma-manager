"""Disposable workers for bounded image decoding and model calls."""
import contextlib
import multiprocessing
import os
import time

from .adapters import AgentError, safe_schema_detail
from . import diagnostics


def _worker(sender, function, args):
    event = diagnostics.forwarding_sink(lambda value: sender.send(('event', value)))
    with diagnostics.scope(event), open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            try:
                sender.send((True, function(*args)))
            except AgentError as error:
                diagnostics.failure(error, boundary='media_worker')
                sender.send((False, (error.code, error.retryable is True,
                    AgentError(diagnostic=error.diagnostic).diagnostic, safe_schema_detail(error.detail))))
            except Exception as error:
                diagnostics.failure(error, boundary='media_worker')
                sender.send((False, ('agent_unavailable', False, 'internal_error', None)))
        finally:
            sender.close()


def bounded(function, args, seconds, timeout_code='agent_timeout'):
    """Only trusted, spawn-compatible functions/clients cross this process boundary."""
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, function, args), daemon=True)
    started = False
    expires = time.monotonic() + seconds
    diagnostics.emit('worker', status='started', boundary='media_worker', timeout_seconds=seconds)
    try:
        process.start()
        started = True
        sender.close()
        while True:
            if not receiver.poll(max(0, expires - time.monotonic())):
                diagnostics.emit('worker', status='timeout', boundary='media_worker', timeout_seconds=seconds)
                raise AgentError(timeout_code, True, 'worker_timeout')
            ok, value = receiver.recv()
            if ok != 'event': break
            diagnostics.emit(value.pop('stage'), **value)
        if not ok:
            error = AgentError(*value[:3])
            error.detail = safe_schema_detail(value[3])
            raise error
        return value
    except AgentError:
        raise
    except (EOFError, BrokenPipeError) as error:
        process.join(timeout=.05)
        diagnostics.failure(error, boundary='media_worker', exit_code=process.exitcode)
        raise AgentError('agent_unavailable', False, 'worker_terminated') from None
    except Exception as error:
        diagnostics.failure(error, boundary='media_worker_transport')
        raise AgentError('agent_unavailable', False, 'internal_error') from None
    finally:
        receiver.close()
        sender.close()
        if started:
            if process.is_alive():
                process.terminate()
            process.join(1)
            if process.is_alive():
                process.kill()
                process.join(1)
            diagnostics.emit('worker_exit', boundary='media_worker', exit_code=process.exitcode)
            process.close()
