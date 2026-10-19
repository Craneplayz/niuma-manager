"""Generation shape only; exact evidence and business rules remain in compiler."""

from .time_validation import normalized_time_schema
from .content_types import content_type_schema

def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def draft_schema(record):
    text = {"type": "string"}
    strings = {"type": "array", "items": text}
    reference = _object({"input_id": text, "quote": text})
    nullable_reference = {"anyOf": [{"type": "null"}, reference]}
    course = {"anyOf": [{"type": "null"}, _object({"value": text, "input_id": text, "quote": text})]}
    # Metadata is copied by the compiler. Do not ask the model to invent a quote
    # for a course that is provided outside the original text.
    if record.get("course_or_project") is not None:
        course = {"type": "null"}
    task = _object({
        "title": text, "description": text, "prerequisites": strings,
        "notice_type": {"type": "string", "enum": ["create", "update", "postpone", "cancel", "uncertain"]},
        "target": nullable_reference, "course": course,
        "evidence": {"type": "array", "items": reference},
        "times": {"type": "array", "items": _object({"input_id": text, "quote": text,
                                                        "kind": {"type": "string", "enum": ["deadline", "reference", "urgency"]},
                                                        "normalized": normalized_time_schema()})},
        "review_reasons": strings,
        "relevance": _object({
            "state": {"type": "string", "enum": ["current", "ongoing", "historical", "uncertain"]},
            "reason": text, "evidence": {"type": "array", "items": reference},
        }),
    })
    if record.get("platform") == "email" and record.get("contract_version") == "0.2":
        task["properties"]["content_type"] = content_type_schema()
        task["required"].append("content_type")
    return _object({"tasks": {"type": "array", "items": task}})
