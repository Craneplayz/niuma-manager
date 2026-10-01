"""Context-aware task decomposition model adapter and draft validation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ddl_text.chat_client import ChatClient
from ddl_text.config import Settings


ROOT = Path(__file__).resolve().parent
SKILL_ROOT = ROOT / "skills" / "contextual-task-decomposition"
MAX_SOURCE_CHARS = 200_000
MAX_CONVERSATION_ITEMS = 100
STATUSES = {"needs_clarification", "ready_to_plan", "no_split"}
COMPLEXITIES = {"uncertain", "multi_step", "atomic"}


def _require_text(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _optional_text(value: Any, path: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{path} must be a string or null")


def _aware_interval(value: Any, path: str) -> tuple[datetime, datetime]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    try:
        start = datetime.fromisoformat(_require_text(value.get("start"), f"{path}.start"))
        end = datetime.fromisoformat(_require_text(value.get("end"), f"{path}.end"))
    except ValueError as error:
        raise ValueError(f"{path} must contain ISO 8601 datetimes") from error
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError(f"{path} must be an increasing interval with UTC offsets")
    return start, end


def validate_request(request: Any) -> dict[str, Any]:
    """Validate the internal request without interpreting task semantics."""
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    if request.get("schema_version") != "0.1-internal":
        raise ValueError("schema_version must be 0.1-internal")
    _require_text(request.get("now"), "now")
    try:
        datetime.fromisoformat(request["now"])
    except ValueError as error:
        raise ValueError("now must be an ISO 8601 datetime") from error
    _require_text(request.get("timezone"), "timezone")

    task = request.get("task")
    if not isinstance(task, dict):
        raise ValueError("task must be an object")
    _require_text(task.get("id"), "task.id")
    _require_text(task.get("title"), "task.title")
    for field in ("description", "deadline", "current_progress", "user_scope"):
        _optional_text(task.get(field), f"task.{field}")

    sources = task.get("source_materials", [])
    if not isinstance(sources, list):
        raise ValueError("task.source_materials must be an array")
    total_chars = 0
    source_ids: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError(f"task.source_materials[{index}] must be an object")
        source_id = _require_text(source.get("id"), f"task.source_materials[{index}].id")
        if source_id in source_ids:
            raise ValueError("task.source_materials ids must be unique")
        source_ids.add(source_id)
        _require_text(source.get("name"), f"task.source_materials[{index}].name")
        text = _require_text(source.get("text"), f"task.source_materials[{index}].text", allow_empty=True)
        total_chars += len(text)
    if total_chars > MAX_SOURCE_CHARS:
        raise ValueError("source material is too large")

    conversation = request.get("conversation", [])
    if not isinstance(conversation, list) or len(conversation) > MAX_CONVERSATION_ITEMS:
        raise ValueError("conversation must be a bounded array")
    for index, message in enumerate(conversation):
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            raise ValueError(f"conversation[{index}] has an invalid role")
        _require_text(message.get("content"), f"conversation[{index}].content")

    preferences = request.get("preferences", {})
    availability = request.get("availability", {})
    if not isinstance(preferences, dict) or not isinstance(availability, dict):
        raise ValueError("preferences and availability must be objects")
    for field in ("windows", "existing_blocks"):
        intervals = availability.get(field, [])
        if not isinstance(intervals, list):
            raise ValueError(f"availability.{field} must be an array")
        for index, interval in enumerate(intervals):
            _aware_interval(interval, f"availability.{field}[{index}]")
    return request


def load_instructions() -> str:
    """Load the concise workflow and the draft contract used for every call."""
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    schema = (SKILL_ROOT / "references" / "model-draft-schema.md").read_text(encoding="utf-8")
    return skill + "\n\n# Required model draft contract\n\n" + schema


def _allowed_sources(request: dict[str, Any] | None) -> set[str] | None:
    if request is None:
        return None
    sources = {"task", "assumption"}
    sources.update(f"conversation:{index}" for index, _ in enumerate(request.get("conversation", [])))
    sources.update(f"source:{source['id']}" for source in request["task"].get("source_materials", []))
    return sources


def _validate_fact_list(value: Any, path: str, allowed_sources: set[str] | None) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    for index, fact in enumerate(value):
        if not isinstance(fact, dict):
            raise ValueError(f"{path}[{index}] must be an object")
        _require_text(fact.get("text"), f"{path}[{index}].text")
        source = _require_text(fact.get("source"), f"{path}[{index}].source")
        if allowed_sources is not None and source not in allowed_sources - {"assumption"}:
            raise ValueError(f"{path}[{index}].source does not exist in the request")


def _validate_plan(plan: Any, status: str, request: dict[str, Any] | None) -> None:
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object when planning is ready")
    _require_text(plan.get("goal"), "plan.goal")
    _require_text(plan.get("strategy"), "plan.strategy")
    warnings = plan.get("warnings")
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise ValueError("plan.warnings must be an array of strings")
    subtasks = plan.get("subtasks")
    if not isinstance(subtasks, list) or not subtasks:
        raise ValueError("plan.subtasks must be a non-empty array")
    if status == "ready_to_plan" and len(subtasks) < 2:
        raise ValueError("ready_to_plan requires at least two subtasks")
    if status == "no_split" and len(subtasks) != 1:
        raise ValueError("no_split requires exactly one subtask")

    ids: set[str] = set()
    allowed_sources = _allowed_sources(request)
    scheduled: dict[str, tuple[datetime, datetime]] = {}
    for index, subtask in enumerate(subtasks):
        path = f"plan.subtasks[{index}]"
        if not isinstance(subtask, dict):
            raise ValueError(f"{path} must be an object")
        subtask_id = _require_text(subtask.get("id"), f"{path}.id")
        if subtask_id in ids:
            raise ValueError("subtask ids must be unique")
        ids.add(subtask_id)
        for field in ("title", "outcome"):
            _require_text(subtask.get(field), f"{path}.{field}")
        basis = subtask.get("source_basis")
        if not isinstance(basis, list) or not basis or any(not isinstance(item, str) for item in basis):
            raise ValueError(f"{path}.source_basis must be a non-empty array of references")
        if allowed_sources is not None and set(basis) - allowed_sources:
            raise ValueError(f"{path}.source_basis contains a reference not present in the request")
        duration = subtask.get("duration_minutes")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            raise ValueError(f"{path}.duration_minutes must be a positive integer")
        if not isinstance(subtask.get("depends_on"), list) or any(
            not isinstance(item, str) for item in subtask["depends_on"]
        ):
            raise ValueError(f"{path}.depends_on must be an array of ids")
        if not isinstance(subtask.get("assumption"), bool):
            raise ValueError(f"{path}.assumption must be a boolean")
        if subtask["assumption"] != ("assumption" in basis):
            raise ValueError(f"{path}.assumption must match source_basis")
        for field in ("scheduled_start", "scheduled_end"):
            _optional_text(subtask.get(field), f"{path}.{field}")
    for index, subtask in enumerate(subtasks):
        unknown = set(subtask["depends_on"]) - ids
        if unknown or subtask["id"] in subtask["depends_on"]:
            raise ValueError(f"plan.subtasks[{index}].depends_on contains an invalid id")
        start, end = subtask.get("scheduled_start"), subtask.get("scheduled_end")
        if (start is None) != (end is None):
            raise ValueError("scheduled_start and scheduled_end must both be set or null")
        if start is not None:
            try:
                start_at, end_at = datetime.fromisoformat(start), datetime.fromisoformat(end)
            except ValueError as error:
                raise ValueError("scheduled times must be ISO 8601 datetimes") from error
            if start_at.tzinfo is None or end_at.tzinfo is None:
                raise ValueError("scheduled times must include a UTC offset")
            if start_at >= end_at:
                raise ValueError("scheduled_start must be earlier than scheduled_end")
            scheduled[subtask["id"]] = (start_at, end_at)

    remaining = {item["id"]: set(item["depends_on"]) for item in subtasks}
    while remaining:
        ready = {identifier for identifier, dependencies in remaining.items() if not dependencies}
        if not ready:
            raise ValueError("subtask dependencies must not contain a cycle")
        remaining = {
            identifier: dependencies - ready
            for identifier, dependencies in remaining.items()
            if identifier not in ready
        }

    if request is None:
        return
    availability = request.get("availability", {})
    windows = [_aware_interval(item, "availability.windows") for item in availability.get("windows", [])]
    existing = [_aware_interval(item, "availability.existing_blocks") for item in availability.get("existing_blocks", [])]
    if scheduled and not windows:
        raise ValueError("scheduled times require supplied availability windows")
    now = datetime.fromisoformat(request["now"])
    deadline_text = request["task"].get("deadline")
    deadline = None
    if deadline_text:
        try:
            candidate = datetime.fromisoformat(deadline_text)
            if candidate.tzinfo is not None:
                deadline = candidate
        except ValueError:
            pass
    ordered = sorted(scheduled.items(), key=lambda item: item[1][0])
    for index, (subtask_id, (start_at, end_at)) in enumerate(ordered):
        if now.tzinfo is not None and start_at < now:
            raise ValueError("scheduled times cannot be in the past")
        if deadline is not None and end_at > deadline:
            raise ValueError("scheduled times cannot be after the deadline")
        if not any(window_start <= start_at and end_at <= window_end for window_start, window_end in windows):
            raise ValueError("scheduled times must fit inside an availability window")
        if any(start_at < block_end and block_start < end_at for block_start, block_end in existing):
            raise ValueError("scheduled times cannot overlap an existing block")
        if index and start_at < ordered[index - 1][1][1]:
            raise ValueError("scheduled subtasks cannot overlap")
        subtask = next(item for item in subtasks if item["id"] == subtask_id)
        for dependency in subtask["depends_on"]:
            if dependency in scheduled and scheduled[dependency][1] > start_at:
                raise ValueError("a scheduled dependency must finish before its dependent subtask")


def validate_model_draft(draft: Any, request: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reject incomplete or internally contradictory model drafts."""
    if not isinstance(draft, dict):
        raise ValueError("model draft must be an object")
    status = draft.get("status")
    if status not in STATUSES:
        raise ValueError("model draft has an invalid status")
    assessment = draft.get("assessment")
    if not isinstance(assessment, dict):
        raise ValueError("assessment must be an object")
    _require_text(assessment.get("summary"), "assessment.summary")
    complexity = assessment.get("complexity")
    expected_complexity = {
        "needs_clarification": "uncertain",
        "ready_to_plan": "multi_step",
        "no_split": "atomic",
    }[status]
    if complexity not in COMPLEXITIES or complexity != expected_complexity:
        raise ValueError("assessment.complexity contradicts status")
    confidence = assessment.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise ValueError("assessment.confidence must be between 0 and 1")
    allowed_sources = _allowed_sources(request)
    _validate_fact_list(assessment.get("known_facts"), "assessment.known_facts", allowed_sources)
    for field in ("assumptions", "missing_information"):
        if not isinstance(assessment.get(field), list) or any(
            not isinstance(item, str) for item in assessment[field]
        ):
            raise ValueError(f"assessment.{field} must be an array of strings")

    questions = draft.get("questions")
    if not isinstance(questions, list) or len(questions) > 3:
        raise ValueError("questions must contain at most three items")
    question_ids: set[str] = set()
    for index, question in enumerate(questions):
        path = f"questions[{index}]"
        if not isinstance(question, dict):
            raise ValueError(f"{path} must be an object")
        question_id = _require_text(question.get("id"), f"{path}.id")
        if question_id in question_ids:
            raise ValueError("question ids must be unique")
        question_ids.add(question_id)
        _require_text(question.get("prompt"), f"{path}.prompt")
        _require_text(question.get("why"), f"{path}.why")
        options = question.get("answer_options")
        if not isinstance(options, list) or any(not isinstance(item, str) for item in options):
            raise ValueError(f"{path}.answer_options must be an array of strings")

    plan = draft.get("plan")
    if status == "needs_clarification":
        if not questions or plan is not None:
            raise ValueError("needs_clarification requires questions and no plan")
    else:
        if questions:
            raise ValueError("ready drafts cannot contain unanswered questions")
        _validate_plan(plan, status, request)
    return draft


def generate_decomposition(request: dict[str, Any], options: dict[str, str]) -> dict[str, Any]:
    """Call the configured model and validate its internal planning draft."""
    validate_request(request)
    settings = Settings(
        mode="real",
        model=options["model"],
        endpoint=options["endpoint"],
        timeout_seconds=20,
        max_retries=0,
    )
    raw = ChatClient(options["key"]).complete_json(
        load_instructions(), json.dumps(request, ensure_ascii=False), settings
    )
    return validate_model_draft(json.loads(raw), request)
