"""F3 routing policy over validated semantic proposals, never raw date formats."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


HISTORY_GRACE_DAYS = 30
SOURCE_PLATFORMS = {"teaching_web", "wechat", "email", "web"}


class CandidateTasks(list):
    """List-compatible internal result; audit strings travel to record_results."""

    def __init__(self, tasks=(), warnings=()):
        super().__init__(tasks)
        self.warnings = list(warnings)


def _as_of(record):
    # A supplied collection snapshot makes replays deterministic. Never use the
    # machine clock, or pretend that collection is the original publication.
    if not record.get("collected_at") or not record.get("timezone"):
        return None
    try:
        return datetime.fromisoformat(record["collected_at"].replace("Z", "+00:00")).astimezone(
            ZoneInfo(record["timezone"]))
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return None


def _past(value, precision, as_of, days=0):
    if not value or as_of is None:
        return False
    if precision == "date":
        # A date-only deadline remains current for the entire source-local day.
        return date.fromisoformat(value) < as_of.date() - timedelta(days=days)
    return datetime.fromisoformat(value.replace("Z", "+00:00")) < as_of - timedelta(days=days)


def classify(task, draft, record):
    """Return (keep, extra review reasons, filter reason), without side effects.

    Historical filtering is confined to v0.2 imported sources. F1 manual input
    and legacy CLI output keep their existing confirmation behavior.
    """
    if record.get("contract_version") != "0.2" or record["platform"] not in SOURCE_PLATFORMS:
        return True, [], None
    if task["notice_type"] in {"update", "postpone", "cancel"}:
        return True, ["变更通知需核对现有任务后处理。"], None
    if task["notice_type"] == "uncertain":
        return True, ["行动或适用对象尚不明确。"], None

    relevance = draft.get("relevance")
    # Old recorded drafts remain replayable but cannot supply semantic evidence
    # for automatic historical exclusion.
    state = relevance["state"] if relevance else "current"
    d, as_of = task["deadline"], _as_of(record)
    collection_year = any(t["kind"] != "urgency" and
                          t["normalized"]["derivation"] == "missing_year" and
                          t["normalized"]["anchor"] == "collected_at" for t in draft["times"])
    dated = d["status"] in {"explicit", "inferred"} and bool(d["value"])
    past = dated and _past(d["value"], d["precision"], as_of)
    future = dated and as_of is not None and not past
    reasons = []
    if state == "ongoing":
        # An explicit outstanding obligation survives even a very old deadline.
        if past:
            reasons.append("通知仍要求完成，但原截止已过，请核对补交或新的时间安排。")
        return True, reasons, None
    if state == "uncertain":
        return True, [relevance["reason"]], None
    if state == "historical":
        if future and not collection_year:
            return True, ["历史描述与尚未到期的日期不一致，请核对通知适用范围。"], None
        if record["text_quality"] != "complete":
            return True, ["正文不完整，不能确认历史行动是否仍有效。"], None
        return False, [], relevance["reason"]

    if collection_year:
        # This is uncertainty about the age of an imported notice, not a request
        # to confirm every omitted year. Trusted publication anchors still pass.
        reasons.append("原通知发布时间缺失，无法判断是否为历史内容；请核对来源。")
    if past:
        old = _past(d["value"], d["precision"], as_of, HISTORY_GRACE_DAYS)
        if old and not collection_year and record["text_quality"] == "complete" and not task["needs_review"]:
            return False, [], "截止 %s 距采集时点已超过 %d 天，未见继续执行要求。" % (d["value"], HISTORY_GRACE_DAYS)
        reasons.append("截止已过，是否仍需执行请核对原通知。")
    if not dated:
        publication = record["published_at"]
        if publication["basis"] == "source" and _past(publication["value"], publication["precision"], as_of, HISTORY_GRACE_DAYS):
            reasons.append("通知发布已超过 %d 天，缺少可确定的有效期限，请核对是否仍需执行。" % HISTORY_GRACE_DAYS)
    return True, reasons, None


def filter_audit(task, draft, reason):
    refs = (draft.get("relevance") or {}).get("evidence") or draft["evidence"]
    # Evidence stays in this user's result, never in server logs. The original
    # input and full quotes remain available in the enclosing request.
    citations = "；".join("%s：%s%s" % (r["input_id"], r["quote"][:200],
                        "…" if len(r["quote"]) > 200 else "") for r in refs)
    return "history_filtered: %s；%s；证据 %s" % (task["candidate_id"], reason, citations)
