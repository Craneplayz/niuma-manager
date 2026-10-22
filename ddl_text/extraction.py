"""Skill-guided semantic drafts -> evidence-grounded v0.1 candidates.

The client boundary has no default provider, network, tools, or credentials.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .adapters import AgentError
from .classification import CandidateTasks, classify, filter_audit
from .contracts import ContractError, nonempty, normalize_record, require, validate_tasks
from .dates import combine_times
from .time_validation import validate_time


SKILL_DIR = Path(__file__).resolve().parent / "skills/text-task-extraction"
MAX_DRAFT_BYTES = 1024 * 1024


class DraftClient(Protocol):
    def generate(self, instructions: str, record_json: str, settings, attempt: int) -> str:
        """Return JSON text. Trusted spawn-compatible client; provider added in #9.

        Keep instructions separate from record data in the provider protocol.
        Disable tools and unsolicited external calls; never log private inputs.
        """
        ...


def load_instructions(record=None):
    # Single maintained rule source shared with the portable Codex skill.
    instructions = "\n\n".join((SKILL_DIR / name).read_text(encoding="utf-8") for name in
                       ("SKILL.md", "references/draft-schema.md", "references/examples.md"))
    if record and record.get("platform") == "email" and record.get("contract_version") == "0.2":
        from .content_types import content_type_instructions
        instructions += "\n\n" + content_type_instructions() + "\n邮箱补充：tasks 数组同时包含 task 和 notice，每项必须输出 content_type。上述仅提取行动、无行动返回空的规则不排除符合共享定义的重要通知。通知按原意命名，通用 evidence 引用通知事实；不编造查看或确认动作。notice_type 仍描述新增/更新/延期/取消操作。普通垃圾邮件摘要、推广或无直接影响的信息返回空 tasks。通知中的活动时间以 reference 表达，不冒充行动截止。"
    return instructions


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
    """Validate semantic proposals and evidence; copy source metadata ourselves."""
    reason = 'schema'
    try:
        require(isinstance(response, str))
        if len(response.encode("utf-8")) > MAX_DRAFT_BYTES:
            error = AgentError("invalid_agent_output", True, 'output_truncated')
            error.telemetry_reason = 'oversized_output'
            raise error
        reason = "invalid_json"
        envelope = json.loads(response, object_pairs_hook=_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        reason = "schema"
        require(isinstance(envelope, dict) and set(envelope) == {"tasks"})
        drafts = envelope["tasks"]
        require(isinstance(drafts, list) and len(drafts) <= 100)
        available = {item["input_id"]: item for item in [record] + record["context"]}
        v02 = record.get("contract_version") == "0.2"
        tasks = CandidateTasks()
        if not drafts and v02:
            tasks.warnings.append("no_action: 未识别到需要执行的行动或变更。")
        for index, draft in enumerate(drafts, 1):
            require(isinstance(draft, dict) and set(draft) - {"relevance", "content_type"} == {"title", "description", "prerequisites",
                    "notice_type", "target", "course", "evidence", "times", "review_reasons"})
            require(nonempty(draft["title"]) and isinstance(draft["description"], str))
            if v02 and record.get("platform") == "email":
                require("content_type" in draft and draft["content_type"] in ("task", "notice"))
            elif "content_type" in draft:
                require(v02 and record.get("platform") == "email" and draft["content_type"] in ("task", "notice"))
            require(draft["notice_type"] in {"create", "update", "postpone", "cancel", "uncertain"})
            for key in ("prerequisites", "review_reasons"):
                require(isinstance(draft[key], list) and all(nonempty(s) for s in draft[key]))
            require(isinstance(draft["evidence"], list) and 0 < len(draft["evidence"]) <= 100)
            reason = 'evidence'
            evidence = [_reference(ref, available) for ref in draft["evidence"]]
            if "relevance" in draft:
                relevance = draft["relevance"]
                require(isinstance(relevance, dict) and set(relevance) == {"state", "reason", "evidence"})
                require(relevance["state"] in {"current", "ongoing", "historical", "uncertain"})
                require(nonempty(relevance["reason"]))
                require(isinstance(relevance["evidence"], list) and len(relevance["evidence"]) <= 20)
                require(relevance["state"] == "current" or bool(relevance["evidence"]))
                evidence.extend(_reference(ref, available) for ref in relevance["evidence"])
            if v02:
                for ref in evidence:
                    ref["supports"] = ["title", "description"]
                    if draft["notice_type"] != "create":
                        ref["supports"].append("notice_type")
                    if any(step in ref["quote"] for step in draft["prerequisites"]):
                        ref["supports"].append("prerequisites")
            require(any(ref["input_id"] == record["input_id"] for ref in evidence))
            # Prerequisites are quotations, unlike the summarized title/description.
            require(all(any(step in ref["quote"] for ref in evidence)
                        for step in draft["prerequisites"]))
            reasons = list(draft["review_reasons"])
            target = draft["target"]
            require(draft["notice_type"] != "create" or target is None)
            if target is not None:
                target_evidence = _reference(target, available)
                if v02:
                    target_evidence["supports"] = ["target_reference"]
                evidence.append(target_evidence)
            elif draft["notice_type"] != "create":
                reasons.append("变更对象未能唯一确定，请查看原通知及上下文。")
            if draft["notice_type"] == "uncertain":
                reasons.append("通知性质尚不明确。")
            course, course_basis = record["course_or_project"], "provided"
            if course is None:
                course_basis = "unknown"
            if draft["course"] is not None:
                course_ref = draft["course"]
                course_evidence = _reference(course_ref, available, ("value",))
                if v02:
                    course_evidence["supports"] = ["course_or_project"]
                evidence.append(course_evidence)
                require(nonempty(course_ref["value"]) and course_ref["value"] in course_ref["quote"])
                if course is None:
                    course, course_basis = course_ref["value"], "inferred"
                    reasons.append("课程归属来自正文或上下文推断，请核实。")
                elif course_ref["value"] != course:
                    reasons.append("正文课程与上游课程标注不一致，保留上游标注待核实。")
            reason = 'time'
            require(isinstance(draft["times"], list) and len(draft["times"]) <= 20)
            times = []
            for ref in draft["times"]:
                time_evidence = _reference(ref, available, ("kind", "normalized"))
                if v02:
                    time_evidence["supports"] = ["deadline"]
                evidence.append(time_evidence)
                times.append(validate_time(ref, available[ref["input_id"]]))
            deadline = combine_times(times, record.get("timezone"))
            if v02:
                if not times and record["text_quality"] == "complete":
                    deadline.update(status="not_provided", basis="完整正文未提供该行动的时间要求。")
                if deadline["kind"] == "reference" and deadline["status"] != "inferred":
                    deadline["kind"] = "unknown"
                    deadline["basis"] += " 引用的是参考活动时间，不能作为明确截止日期。"
                # Do not manufacture a quotation by concatenating separate messages.
                # All time quotes remain individually available in evidence.
                if times and not any(deadline["raw"] in item["text"] for item in available.values()):
                    deadline["raw"] = times[0]["raw"]
                    deadline["basis"] += " 多条时间原话见 evidence 中的 deadline 证据。"
            if deadline["status"] not in {"explicit", "not_provided"}:
                reasons.append(deadline["basis"])
            if record["text_quality"] != "complete":
                reasons.append("正文不完整，行动要求或日期可能缺失。")
            source_ids = list(dict.fromkeys([record["input_id"]] + [e["input_id"] for e in evidence]))
            sources = [{"input_id": key, "platform": available[key].get("platform", "unknown"),
                        "url": available[key]["source_url"], "label": available[key]["source_label"],
                        "source_item_id": available[key].get("source_item_id")} for key in source_ids]
            unique_evidence = []
            for ref in evidence:
                existing = next((e for e in unique_evidence if all(e[k] == ref[k]
                                for k in ("input_id", "field", "quote"))), None)
                if v02 and existing is not None:
                    existing["supports"] = list(dict.fromkeys(existing["supports"] + ref["supports"]))
                elif ref not in unique_evidence:
                    unique_evidence.append(ref)
            task = {"candidate_id": "%s:candidate:%d" % (record["input_id"], index),
                          "input_id": record["input_id"], "title": draft["title"],
                          "description": draft["description"], "prerequisites": list(draft["prerequisites"]),
                          "course_or_project": course, "course_basis": course_basis,
                          "notice_type": draft["notice_type"], "target_reference": target["quote"] if target else None,
                          "deadline": deadline, "needs_review": bool(reasons),
                          "review_reasons": list(dict.fromkeys(reasons)), "sources": sources,
                          "evidence": unique_evidence}
            if v02 and record.get("platform") == "email":
                task["content_type"] = draft["content_type"]
            # Validate even a discarded task, so an invented time/source cannot
            # silently turn an invalid response into successful filtering.
            reason = 'candidate'
            validate_tasks([task], record)
            keep, extra_reasons, filtered = classify(task, draft, record)
            reason = 'schema'
            if not keep:
                tasks.warnings.append(filter_audit(task, draft, filtered))
                continue
            task["review_reasons"] = list(dict.fromkeys(task["review_reasons"] + extra_reasons))
            task["needs_review"] = bool(task["review_reasons"])
            tasks.append(task)
        reason = 'candidate'
        reason = "candidate"
        return validate_tasks(tasks, record)
    except (ContractError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as error:
        diagnostic = 'draft_json' if isinstance(error, (ValueError, json.JSONDecodeError)) else None
        wrapped = AgentError("invalid_agent_output", True, diagnostic)
        if isinstance(error, ValueError) and str(error) == 'duplicate JSON key':
            wrapped.telemetry_reason = 'duplicate_keys'
        elif isinstance(error, json.JSONDecodeError):
            wrapped.telemetry_reason = 'invalid_json'
        elif not getattr(wrapped, 'telemetry_reason', None):
            wrapped.telemetry_reason = reason if reason in {'invalid_json','schema','evidence','time','candidate'} else 'unknown'
        raise wrapped from None


@dataclass
class ExtractionAdapter:
    client: DraftClient

    def extract(self, record, settings, attempt):
        tasks, _ = self.extract_with_metrics(record, settings, attempt)
        return tasks

    def extract_with_metrics(self, record, settings, attempt):
        """Internal worker boundary: return safe timings beside normal tasks."""
        metrics = {'generation_started': False}
        instructions = load_instructions(record)
        started = time.perf_counter()
        metrics['generation_started'] = True
        def capture_usage():
            from .model_usage import TOKEN_FIELDS, FINISH_REASONS, MAX_TOKEN_COUNT
            summary = getattr(self.client, 'response_metrics', None)
            if not isinstance(summary, dict):
                return
            for field in TOKEN_FIELDS:
                value = summary.get(field)
                if type(value) is int and 0 <= value <= MAX_TOKEN_COUNT:
                    metrics[field] = value
            if summary.get('finish_reason') in FINISH_REASONS:
                metrics['finish_reason'] = summary['finish_reason']
            if summary.get('provider_status') == 429:
                metrics['provider_status'] = 429
                seconds = summary.get('retry_after_seconds')
                if type(seconds) in (int, float) and 0 <= seconds <= 300:
                    metrics['retry_after_seconds'] = seconds
        try:
            response = self.client.generate(instructions, json.dumps(record, ensure_ascii=False), settings, attempt)
        except BaseException as error:
            capture_usage()
            metrics.update(generation_ms=(time.perf_counter() - started) * 1000, generation_ok=False)
            try:
                error.telemetry = metrics
            except Exception:
                pass
            raise
        capture_usage()
        metrics.update(generation_ms=(time.perf_counter() - started) * 1000, generation_ok=True)
        started = time.perf_counter()
        try:
            tasks = compile_drafts(response, record)
        except BaseException as error:
            metrics.update(validation_ms=(time.perf_counter() - started) * 1000, validation_ok=False,
                           reason=getattr(error, 'telemetry_reason', _diagnostic_reason(error)))
            try:
                error.telemetry = metrics
            except Exception:
                pass
            raise
        metrics.update(validation_ms=(time.perf_counter() - started) * 1000, validation_ok=True)
        return tasks, metrics


def _diagnostic_reason(error):
    """Map internal fixed diagnostics to coarse, content-free reason labels."""
    diagnostic = getattr(error, 'diagnostic', None)
    mapping = {'draft_json': 'invalid_json', 'output_truncated': 'oversized_output',
               'draft_schema': 'schema', 'draft_evidence': 'evidence',
               'draft_semantics': 'time'}
    return mapping.get(diagnostic, 'unknown')


@dataclass
class OfflineDraftClient:
    """Exact-input synthetic responses; exercises compilation, not NLP accuracy."""
    entries: list

    def generate(self, instructions, record_json, settings, attempt):
        record = json.loads(record_json)
        matches = [entry for entry in self.entries if normalize_record(
            entry["record"], record.get("contract_version", "0.1")) == record]
        if len(matches) != 1:
            raise AgentError("agent_unavailable", False)
        return json.dumps(matches[0]["response"], ensure_ascii=False, allow_nan=False)
