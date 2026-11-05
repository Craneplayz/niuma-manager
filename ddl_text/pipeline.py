"""One versioned request in, one JSON result out; no persistent user state."""

import logging
import time
from dataclasses import replace

from .adapters import AgentError, UnavailableAdapter
from .config import Settings
from .contracts import ContractError, check_request, nonempty, normalize_record, validate_tasks
from .runner import MESSAGES, run_attempt
from contracts.recognition_errors import describe
from . import diagnostics


LOGGER = logging.getLogger("ddl_text")


def error_result(input_id, code, retryable=False, diagnostic=None, detail=None):
    return describe({"input_id": input_id, "code": code, "message": MESSAGES[code], "retryable": retryable}, reason=diagnostic, detail=detail)


@diagnostics.traced
def process_request(request, adapter=None, settings=None, budget_seconds=None, attempt_observer=None, attempt_guard=None, diagnostic_event=None):
    settings = settings or Settings()
    adapter = adapter if adapter is not None else UnavailableAdapter()
    request_id = request.get("request_id") if isinstance(request, dict) else None
    version = "0.2" if isinstance(request, dict) and request.get("contract_version") == "0.2" else "0.1"
    expires = time.monotonic() + budget_seconds if budget_seconds is not None else None
    emit = diagnostics.emit
    result = {"contract_version": version, "request_id": request_id if nonempty(request_id) else None,
              "status": "failed", "record_results": [], "tasks": [], "errors": []}
    try:
        records = check_request(request)
    except ContractError as error:
        result["errors"].append(error_result(None, error.code))
        return result
    used_ids = set()
    for record_index, raw in enumerate(records):
        emit('record', record_index=record_index, status='started')
        started = time.monotonic()
        outcome = {"input_id": raw["input_id"], "status": "failed", "task_ids": [], "warnings": []}
        attempts = 0
        try:
            try:
                record = normalize_record(raw, version)
            except (ContractError, TypeError, ValueError, KeyError):
                raise ContractError("invalid_input") from None
            if record["text_quality"] in {"partial", "summary"}:
                outcome["warnings"].append("incomplete_text")
            attempt = 0
            ordinary_retries = 0
            fallback_used = False
            execution_settings = settings
            while True:
                attempts += 1
                try:
                    if attempt_guard is not None and not attempt_guard():
                        raise AgentError('agent_unavailable',False)
                    attempt_settings = execution_settings
                    if expires is not None:
                        remaining = expires - time.monotonic()
                        if remaining <= 0:
                            raise AgentError("agent_timeout", True)
                        attempt_settings = replace(execution_settings, timeout_seconds=min(execution_settings.timeout_seconds, remaining))
                    tasks = validate_tasks(run_attempt(adapter, record, attempt_settings, attempt, observer=attempt_observer), record)
                    warnings = getattr(tasks, "warnings", [])
                    if not isinstance(warnings, list) or not all(nonempty(w) for w in warnings):
                        raise ContractError("invalid_agent_output")
                    if any(t["candidate_id"] in used_ids for t in tasks):
                        raise ContractError("invalid_agent_output")
                    break
                except ContractError:
                    if ordinary_retries >= settings.max_retries or fallback_used:
                        raise
                    ordinary_retries += 1
                except AgentError as error:
                    if (not fallback_used and settings.truncation_retry_tokens is not None and
                            error.code == 'invalid_agent_output' and error.diagnostic == 'output_truncated' and
                            (getattr(error,'telemetry',None) or {}).get('finish_reason') == 'length'):
                        # Only provider-confirmed truncation gets one larger output allowance.
                        fallback_used = True
                        execution_settings = replace(settings,max_output_tokens=settings.truncation_retry_tokens,
                                                     truncation_retry_tokens=None)
                    elif not error.retryable or ordinary_retries >= settings.max_retries or fallback_used:
                        raise
                    else:
                        ordinary_retries += 1
                attempt += 1
            used_ids.update(task["candidate_id"] for task in tasks)
            result["tasks"].extend(tasks)
            outcome["warnings"].extend(warnings)
            outcome["task_ids"] = [task["candidate_id"] for task in tasks]
            outcome["status"] = "success" if tasks else "no_tasks"
        except ContractError as error:
            diagnostics.failure(error, stage='result_validation', reason='invalid_input' if error.code=='invalid_input' else 'result_contract')
            result["errors"].append(error_result(raw["input_id"], error.code,
                                                error.code == "invalid_agent_output"))
        except AgentError as error:
            emit('text_recognition_failed', code=error.code, diagnostic=error.diagnostic, schema=error.detail)
            result["errors"].append(error_result(raw["input_id"], error.code, error.retryable, error.diagnostic, error.detail))
        except Exception as error:
            diagnostics.failure(error, stage='text_recognition_failed')
            result['errors'].append(error_result(raw['input_id'], 'agent_unavailable', False, 'internal_error'))
        result["record_results"].append(outcome)
        emit('text_result', status=outcome['status'], seconds=time.monotonic()-started)
        LOGGER.info("mode=%s version=%s status=%s attempts=%d elapsed_ms=%d",
                    settings.mode, version, outcome["status"], attempts, (time.monotonic() - started) * 1000)
    failures = sum(item["status"] == "failed" for item in result["record_results"])
    if failures:
        result["status"] = "failed" if failures == len(records) else "partial_success"
    else:
        result["status"] = "success" if result["tasks"] else "no_tasks"
    return result
