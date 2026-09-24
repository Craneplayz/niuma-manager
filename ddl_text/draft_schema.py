"""Generation shape only; exact evidence and business rules remain in compiler."""


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
                                                        "kind": {"type": "string", "enum": ["deadline", "reference"]}})},
        "review_reasons": strings,
    })
    return _object({"tasks": {"type": "array", "items": task}})
