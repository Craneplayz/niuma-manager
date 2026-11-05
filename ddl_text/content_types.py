"""Shared model-owned task/notice taxonomy for teaching and email extraction."""
from pathlib import Path


def content_type_schema():
    return {"type": "string", "enum": ["task", "notice"]}


def content_type_instructions():
    return (Path(__file__).parent / "skills/content-types.md").read_text(encoding="utf-8")
