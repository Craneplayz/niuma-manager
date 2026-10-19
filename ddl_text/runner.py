"""Bound each trusted adapter attempt in a disposable process."""

import contextlib
import json
import multiprocessing
import math
import os
import time

from .adapters import AgentError, safe_schema_detail
from .classification import CandidateTasks


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
    from .extraction import ExtractionAdapter
    # Prevent adapter debug prints and exceptions from leaking private input.
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            metrics = None
            try:
                if type(adapter) is ExtractionAdapter:
                    tasks, metrics = adapter.extract_with_metrics(record, settings, attempt)
                else:
                    tasks = adapter.extract(record, settings, attempt)
                payload = {"ok": True, "tasks": tasks, "warnings": getattr(tasks, "warnings", []),
                           "metrics": metrics}
                try:
                    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                except (TypeError, ValueError, UnicodeError):
                    raise AgentError("invalid_agent_output", False) from None
                if len(encoded) > MAX_RESPONSE_BYTES:
                    raise AgentError("invalid_agent_output", False)
            except AgentError as error:
                metrics = getattr(error, 'telemetry', None)
                code = error.code if isinstance(error.code, str) and error.code in AGENT_CODES else "agent_unavailable"
                encoded = json.dumps({"ok": False, "code": code,
                                      "retryable": error.retryable is True,
                                      "diagnostic": AgentError(diagnostic=error.diagnostic).diagnostic,
                                      "detail": safe_schema_detail(error.detail),
                                      "metrics": metrics}).encode("utf-8")
            except Exception as error:
                # Never serialize exception messages, stacks or arbitrary adapter attributes.
                metrics = getattr(error, 'telemetry', None) if type(adapter) is ExtractionAdapter else None
                encoded = json.dumps({"ok": False, "code": "agent_unavailable", "retryable": False,
                                      "metrics": metrics}).encode("utf-8")
            connection.send_bytes(encoded)
        finally:
            connection.close()


def _notify(observer, metrics):
    if not callable(observer) or not isinstance(metrics, dict):
        return
    reasons = {'invalid_json','duplicate_keys','oversized_output','schema','evidence','time','candidate','unknown'}
    safe = {}
    for key in ('generation_started', 'generation_ok', 'validation_ok'):
        if type(metrics.get(key)) is bool:
            safe[key] = metrics[key]
    for key in ('generation_ms', 'validation_ms'):
        value = metrics.get(key)
        try:
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                safe[key] = value
        except OverflowError:
            pass
    from .model_usage import TOKEN_FIELDS, FINISH_REASONS, MAX_TOKEN_COUNT
    for key in TOKEN_FIELDS:
        value = metrics.get(key)
        if type(value) is int and 0 <= value <= MAX_TOKEN_COUNT:
            safe[key] = value
    if metrics.get('finish_reason') in FINISH_REASONS:
        safe['finish_reason'] = metrics['finish_reason']
    if metrics.get('provider_status') == 429:
        safe['provider_status'] = 429
        seconds = metrics.get('retry_after_seconds')
        if type(seconds) in (int, float) and math.isfinite(seconds) and 0 <= seconds <= 300:
            safe['retry_after_seconds'] = seconds
    reason = metrics.get('reason')
    if isinstance(reason, str) and reason in reasons:
        safe['reason'] = reason
    try:
        observer(safe)
    except Exception:
        pass


def run_attempt(adapter, record, settings, attempt, observer=None):
    # This trusted transport owns and reaps its Codex subprocess itself. Killing
    # an intermediate multiprocessing worker would orphan the billed request.
    from .extraction import ExtractionAdapter
    from .codex_client import CodexClient
    if type(adapter) is ExtractionAdapter and type(adapter.client) is CodexClient:
        try:
            tasks, metrics = adapter.extract_with_metrics(record, settings, attempt)
            _notify(observer, metrics)
            return tasks
        except AgentError as error:
            _notify(observer, getattr(error, 'telemetry', None))
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
        _notify(observer, response.get('metrics'))
        if not response["ok"]:
            error = AgentError(response["code"], response["retryable"], response.get('diagnostic'))
            error.detail = safe_schema_detail(response.get('detail'))
            error.telemetry = response.get('metrics')
            raise error
        # Do not coerce a malformed adapter output (e.g. a dict) into a list.
        if not isinstance(response["tasks"], list) or not isinstance(response.get("warnings", []), list):
            raise AgentError("invalid_agent_output", True)
        return CandidateTasks(response["tasks"], response.get("warnings", []))
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
