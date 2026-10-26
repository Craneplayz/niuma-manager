"""Bound each trusted adapter attempt in a disposable process."""

import contextlib
import json
import multiprocessing
import os
import time

from .adapters import AgentError, safe_schema_detail
from .classification import CandidateTasks
from . import diagnostics


MESSAGES = {
    "invalid_request": "请求格式无效、输入标识重复或批量超过限制。",
    "unsupported_version": "不支持此接口版本。",
    "invalid_input": "输入文本或元数据无效。",
    "agent_unavailable": "Agent 不可用、未配置，或离线输入没有对应回放样例。",
    "agent_timeout": "Agent 调用超时。",
    "invalid_agent_output": "Agent 结果未通过格式或来源证据校验。",
}
AGENT_CODES = {"agent_unavailable", "agent_timeout", "invalid_agent_output"}
MAX_RESPONSE_BYTES = 1024 * 1024


def _worker(connection, adapter, record, settings, attempt):
    # Prevent adapter debug prints and exceptions from leaking private input.
    event = diagnostics.forwarding_sink(lambda value: connection.send_bytes(json.dumps(dict(event=value)).encode('utf-8')))
    with diagnostics.scope(event), open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            try:
                tasks = adapter.extract(record, settings, attempt)
                payload = {"ok": True, "tasks": tasks, "warnings": getattr(tasks, "warnings", [])}
                try:
                    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                except (TypeError, ValueError, UnicodeError):
                    raise AgentError("invalid_agent_output", False) from None
                if len(encoded) > MAX_RESPONSE_BYTES:
                    raise AgentError("invalid_agent_output", False)
            except AgentError as error:
                diagnostics.failure(error, boundary='text_worker')
                code = error.code if isinstance(error.code, str) and error.code in AGENT_CODES else "agent_unavailable"
                encoded = json.dumps({"ok": False, "code": code,
                                      "retryable": error.retryable is True,
                                      "diagnostic": AgentError(diagnostic=error.diagnostic).diagnostic,
                                      "detail": safe_schema_detail(error.detail)}).encode("utf-8")
            except Exception as error:
                diagnostics.failure(error, boundary='text_worker')
                encoded = b'{"ok":false,"code":"agent_unavailable","retryable":false,"diagnostic":"internal_error"}'
            connection.send_bytes(encoded)
        finally:
            connection.close()


def run_attempt(adapter, record, settings, attempt):
    # This trusted transport owns and reaps its Codex subprocess itself. Killing
    # an intermediate multiprocessing worker would orphan the billed request.
    from .extraction import ExtractionAdapter
    from .codex_client import CodexClient
    if type(adapter) is ExtractionAdapter and type(adapter.client) is CodexClient:
        try:
            return adapter.extract(record, settings, attempt)
        except AgentError:
            raise
        except Exception as error:
            diagnostics.failure(error, boundary='direct_adapter')
            raise AgentError("agent_unavailable", False, 'internal_error') from None
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, adapter, record, settings, attempt), daemon=True)
    started = False
    deadline = time.monotonic() + settings.timeout_seconds
    diagnostics.emit('worker', status='started', timeout_seconds=settings.timeout_seconds, boundary='text_worker')
    try:
        process.start()
        started = True
        sender.close()
        while True:
            if not receiver.poll(max(0, deadline - time.monotonic())):
                diagnostics.emit('worker', status='timeout', boundary='text_worker', timeout_seconds=settings.timeout_seconds)
                raise AgentError("agent_timeout", True, 'worker_timeout')
            response = json.loads(receiver.recv_bytes(MAX_RESPONSE_BYTES).decode("utf-8"))
            if 'event' not in response: break
            event = response['event']
            diagnostics.emit(event.pop('stage'), **event)
        if not response["ok"]:
            error = AgentError(response["code"], response["retryable"], response.get('diagnostic'))
            error.detail = safe_schema_detail(response.get('detail'))
            raise error
        # Do not coerce a malformed adapter output (e.g. a dict) into a list.
        if not isinstance(response["tasks"], list) or not isinstance(response.get("warnings", []), list):
            raise AgentError("invalid_agent_output", True)
        return CandidateTasks(response["tasks"], response.get("warnings", []))
    except AgentError:
        raise
    except (EOFError, BrokenPipeError) as error:
        process.join(timeout=.05)
        diagnostics.failure(error, boundary='text_worker', exit_code=process.exitcode)
        raise AgentError('agent_unavailable', False, 'worker_terminated') from None
    except Exception as error:
        diagnostics.failure(error, boundary='text_worker_transport')
        raise AgentError("agent_unavailable", False, 'internal_error') from None
    finally:
        receiver.close()
        sender.close()
        if started:
            # A hung/crashed adapter cannot hold later records or CLI shutdown.
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            diagnostics.emit('worker_exit', boundary='text_worker', exit_code=process.exitcode)
            process.close()
