"""One request in, one v0.1 result out; no persistent user state."""

import logging
import time

from .adapters import AgentError, UnavailableAdapter
from .config import Settings
from .contracts import ContractError, check_request, nonempty, normalize_record, validate_tasks
from .runner import MESSAGES, run_attempt


LOGGER = logging.getLogger("ddl_text")


def error_result(input_id, code, retryable=False):
    return {"input_id": input_id, "code": code, "message": MESSAGES[code], "retryable": retryable}


def process_request(request, adapter=None, settings=None):
    settings = settings or Settings()
    adapter = adapter if adapter is not None else UnavailableAdapter()
    request_id = request.get("request_id") if isinstance(request, dict) else None
    result = {"contract_version": "0.1", "request_id": request_id if nonempty(request_id) else None,
              "status": "failed", "record_results": [], "tasks": [], "errors": []}
    try:
        records = check_request(request)
    except ContractError as error:
        result["errors"].append(error_result(None, error.code))
        return result
    used_ids = set()
    for raw in records:
        started = time.monotonic()
        outcome = {"input_id": raw["input_id"], "status": "failed", "task_ids": [], "warnings": []}
        attempts = 0
        try:
            try:
                record = normalize_record(raw)
            except (ContractError, TypeError, ValueError, KeyError):
                raise ContractError("invalid_input") from None
            if record["text_quality"] in {"partial", "summary"}:
                outcome["warnings"].append("incomplete_text")
            for attempt in range(settings.max_retries + 1):
                attempts += 1
                try:
                    tasks = validate_tasks(run_attempt(adapter, record, settings, attempt), record)
                    if any(t["candidate_id"] in used_ids for t in tasks):
                        raise ContractError("invalid_agent_output")
                    break
                except ContractError:
                    if attempt == settings.max_retries:
                        raise
                except AgentError as error:
                    if not error.retryable or attempt == settings.max_retries:
                        raise
            used_ids.update(task["candidate_id"] for task in tasks)
            result["tasks"].extend(tasks)
            outcome["task_ids"] = [task["candidate_id"] for task in tasks]
            outcome["status"] = "success" if tasks else "no_tasks"
        except ContractError as error:
            result["errors"].append(error_result(raw["input_id"], error.code,
                                                error.code == "invalid_agent_output"))
        except AgentError as error:
            result["errors"].append(error_result(raw["input_id"], error.code, error.retryable))
        result["record_results"].append(outcome)
        LOGGER.info("mode=%s version=0.1 status=%s attempts=%d elapsed_ms=%d",
                    settings.mode, outcome["status"], attempts, (time.monotonic() - started) * 1000)
    failures = sum(item["status"] == "failed" for item in result["record_results"])
    if failures:
        result["status"] = "failed" if failures == len(records) else "partial_success"
    else:
        result["status"] = "success" if result["tasks"] else "no_tasks"
    return result
