"""Disposable workers for bounded image decoding and model calls."""
import contextlib
import multiprocessing
import os
import time

from .adapters import AgentError, safe_schema_detail


def _worker(sender, function, args):
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            try:
                sender.send((True, function(*args)))
            except AgentError as error:
                sender.send((False, (error.code, error.retryable is True,
                    AgentError(diagnostic=error.diagnostic).diagnostic, safe_schema_detail(error.detail))))
            except Exception:
                sender.send((False, ('agent_unavailable', False, None, None)))
        finally:
            sender.close()


def bounded(function, args, seconds, timeout_code='agent_timeout'):
    """Only trusted, spawn-compatible functions/clients cross this process boundary."""
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, function, args), daemon=True)
    started = False
    expires = time.monotonic() + seconds
    try:
        process.start()
        started = True
        sender.close()
        if not receiver.poll(max(0, expires - time.monotonic())):
            raise AgentError(timeout_code, True)
        ok, value = receiver.recv()
        if not ok:
            error = AgentError(*value[:3])
            error.detail = safe_schema_detail(value[3])
            raise error
        return value
    except AgentError:
        raise
    except (EOFError, OSError, ValueError):
        raise AgentError('agent_unavailable', False) from None
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
            process.close()
