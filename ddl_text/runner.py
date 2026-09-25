"""Bound each trusted adapter attempt in a disposable process."""

import contextlib
import json
import multiprocessing
import os
import time

from .adapters import AgentError


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
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            try:
                payload = {"ok": True, "tasks": adapter.extract(record, settings, attempt)}
                try:
                    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                except (TypeError, ValueError, UnicodeError):
                    raise AgentError("invalid_agent_output", False) from None
                if len(encoded) > MAX_RESPONSE_BYTES:
                    raise AgentError("invalid_agent_output", False)
            except AgentError as error:
                code = error.code if isinstance(error.code, str) and error.code in AGENT_CODES else "agent_unavailable"
                encoded = json.dumps({"ok": False, "code": code,
                                      "retryable": error.retryable is True}).encode("utf-8")
            except Exception:
                # Do not serialize exception messages, stack traces or secrets.
                encoded = b'{"ok":false,"code":"agent_unavailable","retryable":false}'
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
        except Exception:
            raise AgentError("agent_unavailable", False) from None
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, adapter, record, settings, attempt), daemon=True)
    started = False
    deadline = time.monotonic() + settings.timeout_seconds
    try:
        process.start()
        started = True
        sender.close()
        if not receiver.poll(max(0, deadline - time.monotonic())):
            raise AgentError("agent_timeout", True)
        response = json.loads(receiver.recv_bytes(MAX_RESPONSE_BYTES).decode("utf-8"))
        if not response["ok"]:
            raise AgentError(response["code"], response["retryable"])
        return response["tasks"]
    except AgentError:
        raise
    except Exception:
        raise AgentError("agent_unavailable", False) from None
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
            process.close()
