"""Skill-guided semantic drafts -> evidence-grounded v0.1 candidates.

The client boundary has no default provider, network, tools, or credentials.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .adapters import AgentError
from .contracts import ContractError, nonempty, normalize_record, require, validate_tasks
from .dates import combine_times, resolve_time


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills/text-task-extraction"
MAX_DRAFT_BYTES = 1024 * 1024


class DraftClient(Protocol):
    def generate(self, instructions: str, record_json: str, settings, attempt: int) -> str:
        """Return JSON text. Trusted spawn-compatible client; provider added in #9.

        Keep instructions separate from record data in the provider protocol.
        Disable tools and unsolicited external calls; never log private inputs.
        """
        ...


def load_instructions():
    # Single maintained rule source shared with the portable Codex skill.
    return "\n\n".join((SKILL_DIR / name).read_text(encoding="utf-8") for name in
                       ("SKILL.md", "references/draft-schema.md", "references/examples.md"))


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reference(ref, available, extra=()):
    require(isinstance(ref, dict) and set(ref) == {"input_id", "quote"} | set(extra))
    require(isinstance(ref["input_id"], str) and ref["input_id"] in available)
    require(nonempty(ref["quote"]) and ref["quote"] in available[ref["input_id"]]["text"])
    return {"input_id": ref["input_id"], "field": "text", "quote": ref["quote"]}


def compile_drafts(response, record):
    """Never accept model-authored URLs, timestamps, or silent dropped tasks."""
    try:
        require(isinstance(response, str) and len(response.encode("utf-8")) <= MAX_DRAFT_BYTES)
        envelope = json.loads(response, object_pairs_hook=_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        require(isinstance(envelope, dict) and set(envelope) == {"tasks"})
        drafts = envelope["tasks"]
        require(isinstance(drafts, list) and len(drafts) <= 100)
        available = {item["input_id"]: item for item in [record] + record["context"]}
        tasks = []
        for index, draft in enumerate(drafts, 1):
            require(isinstance(draft, dict) and set(draft) == {"title", "description", "prerequisites",
                    "notice_type", "target", "course", "evidence", "times", "review_reasons"})
            require(nonempty(draft["title"]) and isinstance(draft["description"], str))
            require(draft["notice_type"] in {"create", "update", "postpone", "cancel", "uncertain"})
            for key in ("prerequisites", "review_reasons"):
                require(isinstance(draft[key], list) and all(nonempty(s) for s in draft[key]))
            require(isinstance(draft["evidence"], list) and 0 < len(draft["evidence"]) <= 100)
            evidence = [_reference(ref, available) for ref in draft["evidence"]]
            require(any(ref["input_id"] == record["input_id"] for ref in evidence))
            reasons = list(draft["review_reasons"])
            target = draft["target"]
            require(draft["notice_type"] != "create" or target is None)
            if target is not None:
                evidence.append(_reference(target, available))
            elif draft["notice_type"] != "create":
                reasons.append("变更对象未能唯一确定，请查看原通知及上下文。")
            if draft["notice_type"] == "uncertain":
                reasons.append("通知性质尚不明确。")
            course, course_basis = record["course_or_project"], "provided"
            if course is None:
                course_basis = "unknown"
            if draft["course"] is not None:
                course_ref = draft["course"]
                evidence.append(_reference(course_ref, available, ("value",)))
                require(nonempty(course_ref["value"]) and course_ref["value"] in course_ref["quote"])
                if course is None:
                    course, course_basis = course_ref["value"], "inferred"
                    reasons.append("课程归属来自正文或上下文推断，请核实。")
                elif course_ref["value"] != course:
                    reasons.append("正文课程与上游课程标注不一致，保留上游标注待核实。")
            require(isinstance(draft["times"], list) and len(draft["times"]) <= 20)
            times = []
            for ref in draft["times"]:
                evidence.append(_reference(ref, available, ("kind",)))
                require(ref["kind"] in {"deadline", "reference"})
                times.append(resolve_time(ref["quote"], available[ref["input_id"]], ref["kind"]))
            deadline = combine_times(times)
            if deadline["status"] != "explicit":
                reasons.append(deadline["basis"])
            if record["text_quality"] != "complete":
                reasons.append("正文不完整，行动要求或日期可能缺失。")
            source_ids = list(dict.fromkeys([record["input_id"]] + [e["input_id"] for e in evidence]))
            sources = [{"input_id": key, "platform": available[key].get("platform", "unknown"),
                        "url": available[key]["source_url"], "label": available[key]["source_label"],
                        "source_item_id": available[key].get("source_item_id")} for key in source_ids]
            unique_evidence = []
            for ref in evidence:
                if ref not in unique_evidence:
                    unique_evidence.append(ref)
            tasks.append({"candidate_id": "%s:candidate:%d" % (record["input_id"], index),
                          "input_id": record["input_id"], "title": draft["title"],
                          "description": draft["description"], "prerequisites": list(draft["prerequisites"]),
                          "course_or_project": course, "course_basis": course_basis,
                          "notice_type": draft["notice_type"], "target_reference": target["quote"] if target else None,
                          "deadline": deadline, "needs_review": bool(reasons),
                          "review_reasons": list(dict.fromkeys(reasons)), "sources": sources,
                          "evidence": unique_evidence})
        return validate_tasks(tasks, record)
    except (ContractError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        raise AgentError("invalid_agent_output", True) from None


@dataclass
class ExtractionAdapter:
    client: DraftClient

    def extract(self, record, settings, attempt):
        instructions = load_instructions()
        response = self.client.generate(instructions, json.dumps(record, ensure_ascii=False), settings, attempt)
        return compile_drafts(response, record)


@dataclass
class OfflineDraftClient:
    """Exact-input synthetic responses; exercises compilation, not NLP accuracy."""
    entries: list

    def generate(self, instructions, record_json, settings, attempt):
        record = json.loads(record_json)
        matches = [entry for entry in self.entries if normalize_record(entry["record"]) == record]
        if len(matches) != 1:
            raise AgentError("agent_unavailable", False)
        return json.dumps(matches[0]["response"], ensure_ascii=False, allow_nan=False)
