"""Structural boundary checks; semantic extraction remains the agent's job."""

from copy import deepcopy
from datetime import date, datetime


class ContractError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code="invalid_input"):
    if not condition:
        raise ContractError(code)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def nullable_text(value):
    return value is None or isinstance(value, str)


def valid_time(value, precision):
    try:
        if precision == "unknown":
            return value is None
        if precision == "date":
            return date.fromisoformat(value).isoformat() == value
        if precision == "datetime":
            return "T" in value and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except (TypeError, ValueError, AttributeError):
        pass
    return False


def check_publication(value):
    require(isinstance(value, dict))
    require(valid_time(value.get("value"), value.get("precision")))
    require(value.get("basis") in {"source", "upstream_unverified", "unknown"})
    require((value["precision"] == "unknown") == (value["basis"] == "unknown"))


def check_request(request):
    require(isinstance(request, dict), "invalid_request")
    require(request.get("contract_version") == "0.1", "unsupported_version")
    require(nonempty(request.get("request_id")), "invalid_request")
    records = request.get("records")
    require(isinstance(records, list) and len(records) <= 100, "invalid_request")
    ids = []
    for record in records:
        require(isinstance(record, dict) and nonempty(record.get("input_id")), "invalid_request")
        ids.append(record["input_id"])
    require(len(ids) == len(set(ids)), "invalid_request")
    return records


def normalize_record(record):
    result = {
        "text_quality": "complete", "platform": "unknown",
        "published_at": {"value": None, "precision": "unknown", "basis": "unknown"},
        "collected_at": None, "timezone": None, "timezone_basis": "unknown",
        "source_url": None, "source_label": None, "source_item_id": None,
        "course_or_project": None, "context": [], "upstream_hints": None,
    }
    # Retain known fields only; unknown extensions are not instructions to an agent.
    for key in list(result) + ["input_id", "text"]:
        if key in record:
            result[key] = deepcopy(record[key])
    require(nonempty(result.get("text")) and len(result["text"]) <= 200000)
    require(result["text_quality"] in {"complete", "partial", "summary"})
    require(result["platform"] in {"teaching_web", "wechat", "email", "web", "unknown"})
    check_publication(result["published_at"])
    require(result["collected_at"] is None or valid_time(result["collected_at"], "datetime"))
    require(nullable_text(result["timezone"]))
    require(result["timezone_basis"] in {"source", "caller_default", "unknown"})
    require((result["timezone_basis"] == "unknown") == (result["timezone"] is None))
    for key in ("source_url", "source_label", "source_item_id", "course_or_project"):
        require(nullable_text(result[key]))
    require(result["upstream_hints"] is None or isinstance(result["upstream_hints"], dict))
    require(isinstance(result["context"], list) and len(result["context"]) <= 100)
    ids = {result["input_id"]}
    for context in result["context"]:
        require(isinstance(context, dict) and nonempty(context.get("input_id")))
        require(context["input_id"] not in ids)
        ids.add(context["input_id"])
        require(nonempty(context.get("text")) and len(context["text"]) <= 200000)
        check_publication(context.get("published_at"))
        for key in ("timezone", "source_url", "source_label"):
            require(key in context and nullable_text(context[key]))
    return result


def validate_tasks(tasks, record):
    """Reject the whole record if any candidate is structurally unusable."""
    try:
        require(isinstance(tasks, list) and len(tasks) <= 100)
        available = {r["input_id"]: r for r in [record] + record["context"]}
        seen = set()
        required = {"candidate_id", "input_id", "title", "description", "prerequisites",
                    "course_or_project", "course_basis", "notice_type", "target_reference",
                    "deadline", "needs_review", "review_reasons", "sources", "evidence"}
        for task in tasks:
            require(isinstance(task, dict) and required == task.keys())
            require(nonempty(task["candidate_id"]) and task["candidate_id"] not in seen)
            seen.add(task["candidate_id"])
            require(task["input_id"] == record["input_id"] and nonempty(task["title"]))
            require(isinstance(task["description"], str))
            for key in ("prerequisites", "review_reasons"):
                require(isinstance(task[key], list) and all(nonempty(s) for s in task[key]))
            require(nullable_text(task["course_or_project"]) and nullable_text(task["target_reference"]))
            require(task["course_basis"] in {"provided", "inferred", "unknown"})
            require((task["course_basis"] == "unknown") == (task["course_or_project"] is None))
            require(task["notice_type"] in {"create", "update", "postpone", "cancel", "uncertain"})
            require(type(task["needs_review"]) is bool)
            require(task["needs_review"] == bool(task["review_reasons"]))
            if task["course_basis"] == "provided":
                require(task["course_or_project"] == record["course_or_project"])
            if task["course_basis"] == "inferred" or task["notice_type"] == "uncertain":
                require(task["needs_review"])
            if task["notice_type"] == "create":
                require(task["target_reference"] is None)
            elif not nonempty(task["target_reference"]):
                require(task["needs_review"])
            deadline = task["deadline"]
            require(isinstance(deadline, dict))
            require({"value", "precision", "timezone", "kind", "status", "raw", "basis", "alternatives"} == deadline.keys())
            require(valid_time(deadline["value"], deadline["precision"]))
            require(nullable_text(deadline["timezone"]) and nullable_text(deadline["raw"]))
            require(nonempty(deadline["basis"]))
            require(deadline["kind"] in {"deadline", "reference", "unknown"})
            require(deadline["status"] in {"explicit", "inferred", "unknown", "conflict"})
            require(isinstance(deadline["alternatives"], list) and all(nonempty(s) for s in deadline["alternatives"]))
            require(all(valid_time(s, "date") or valid_time(s, "datetime") for s in deadline["alternatives"]))
            require(bool(deadline["alternatives"]) == (deadline["status"] == "conflict"))
            if deadline["status"] == "conflict":
                require(len(set(deadline["alternatives"])) >= 2)
            require((deadline["value"] is None) == (deadline["status"] in {"unknown", "conflict"}))
            if deadline["value"] is not None:
                require(deadline["kind"] != "unknown")
            if deadline["precision"] == "datetime":
                require(nonempty(deadline["timezone"]))
            require(deadline["kind"] != "reference" or deadline["status"] in {"inferred", "unknown", "conflict"})
            require(deadline["status"] == "explicit" or task["needs_review"])
            for key in ("sources", "evidence"):
                require(isinstance(task[key], list) and bool(task[key]))
            for source in task["sources"]:
                require(isinstance(source, dict) and set(source) == {"input_id", "platform", "url", "label", "source_item_id"})
                original = available[source["input_id"]]
                require(source["url"] == original["source_url"])
                require(source["label"] == original["source_label"])
                require(source["source_item_id"] == original.get("source_item_id"))
                require(source["platform"] == original.get("platform", "unknown"))
            for evidence in task["evidence"]:
                require(isinstance(evidence, dict) and set(evidence) == {"input_id", "field", "quote"})
                original = available[evidence["input_id"]]
                require(nonempty(evidence["field"]) and nonempty(evidence["quote"]))
                for segment in evidence["field"].split("."):
                    original = original[segment]
                require(isinstance(original, str) and evidence["quote"] in original)
        return deepcopy(tasks)
    except (ContractError, KeyError, TypeError, ValueError, AttributeError):
        raise ContractError("invalid_agent_output") from None
