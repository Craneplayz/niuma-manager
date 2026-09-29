"""Validate model-normalized dates, without interpreting the quotation's format.

Evidence membership is checked by extraction._reference. These checks establish
structural consistency, not that a model understood a sentence correctly.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import nonempty, require
from .contract_v02 import time_value


NORMALIZED_FIELDS = {"value", "precision", "derivation", "anchor", "basis"}
DERIVATIONS = ("absolute", "relative", "offset", "missing_year", "unresolved")


def normalized_time_schema():
    properties = {
        "value": {"type": ["string", "null"]},
        "precision": {"type": "string", "enum": ["date", "datetime", "unknown"]},
        "derivation": {"type": "string", "enum": list(DERIVATIONS)},
        "anchor": {"type": ["string", "null"], "enum": [None, "published_at", "collected_at"]},
        "basis": {"type": "string"},
    }
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _zone(name):
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None


def _publication(message, zone):
    publication = message["published_at"]
    if publication["basis"] != "source":
        return None
    if publication["precision"] == "date":
        return date.fromisoformat(publication["value"])
    if publication["precision"] == "datetime" and zone is not None:
        return datetime.fromisoformat(publication["value"].replace("Z", "+00:00")).astimezone(zone)
    return None


def _nearest_year(day, anchor):
    choices = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            choices.append(day.replace(year=year))
        except ValueError:
            pass
    return min(choices, key=lambda candidate: (abs((candidate - anchor).days), candidate < anchor))


def validate_time(ref, message):
    """Keep the action reviewable when a well-shaped date proposal is invalid.

    Missing fields/types remain protocol errors. No raw phrase is parsed here;
    derivation and role are semantic judgments supplied by the model.
    """
    require(ref["kind"] in {"deadline", "reference", "urgency"})
    n = ref["normalized"]
    require(isinstance(n, dict) and set(n) == NORMALIZED_FIELDS)
    require(n["value"] is None or isinstance(n["value"], str))
    require(n["precision"] in {"date", "datetime", "unknown"})
    require(n["derivation"] in DERIVATIONS)
    require(n["anchor"] in (None, "published_at", "collected_at"))
    require(nonempty(n["basis"]))
    zone = _zone(message.get("timezone"))
    result = {"value": None, "precision": "unknown", "timezone": zone.key if zone else None,
              "kind": ref["kind"], "status": "unknown", "raw": ref["quote"],
              "basis": n["basis"], "alternatives": []}

    def unresolved(reason):
        return dict(result, basis=reason)

    if n["derivation"] == "unresolved" or ref["kind"] == "urgency":
        if n["value"] is not None or n["precision"] != "unknown" or n["anchor"] is not None:
            return unresolved("未确定的时间不能同时包含确定日期或计算基准。")
        return result
    if n["value"] is None or n["precision"] == "unknown":
        return unresolved("时间归一化缺少具体值或精度。")
    try:
        # Only the standard output format is validated, never the source wording.
        time_value(n["value"], n["precision"])
        if n["precision"] == "datetime":
            if zone is None:
                return unresolved("具体时刻缺少可用的来源时区。")
            stamp = datetime.fromisoformat(n["value"].replace("Z", "+00:00"))
            local = stamp.astimezone(zone)
            if stamp.utcoffset() != local.utcoffset():
                return unresolved("标准时间的偏移与来源时区不一致。")
            naive = local.replace(tzinfo=None)
            offsets = {candidate.utcoffset() for fold in (0, 1)
                       for candidate in [naive.replace(tzinfo=zone, fold=fold)]
                       if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive}
            if len(offsets) != 1:
                return unresolved("本地时刻处于夏令时重复范围，需要核对具体时区偏移。")
            day = local.date()
            value = local.isoformat()
        else:
            day = date.fromisoformat(n["value"])
            value = day.isoformat()
        derivation, field = n["derivation"], n["anchor"]
        if derivation == "absolute":
            if field is not None:
                return unresolved("完整绝对日期不应依赖发布时间或采集时间。")
            basis = "按原文明确时间归一化。"
        else:
            publication = _publication(message, zone)
            if derivation == "missing_year":
                expected = "published_at" if publication is not None else "collected_at"
                if field != expected:
                    return unresolved("补年须优先引用该消息的可信发布时间，其次才是采集时间。")
                anchor = publication
                if field == "collected_at":
                    if not message.get("collected_at") or zone is None:
                        return unresolved("缺少年份且该消息没有可用的日期基准。")
                    anchor = datetime.fromisoformat(message["collected_at"].replace("Z", "+00:00")).astimezone(zone)
                anchor_day = anchor.date() if isinstance(anchor, datetime) else anchor
                if day != _nearest_year(day, anchor_day):
                    return unresolved("补全的年份不符合最近有效日期规则。")
                basis = "按%s %s 补全年份。" % ("发布时间" if field == "published_at" else "采集时间", anchor_day.isoformat())
            else:
                if field != "published_at" or publication is None:
                    return unresolved("相对时间需要该原消息的可信发布时间；采集时间不能代替。")
                if derivation == "offset" and not isinstance(publication, datetime):
                    return unresolved("小时或分钟偏移需要精确到时刻的原消息发布时间。")
                basis = "按该消息发布时间 %s 换算。" % publication.isoformat()
        if ref["kind"] == "reference":
            basis += " 取活动时间作为参考，并非明确截止。"
        return dict(result, value=value, precision=n["precision"], basis=basis,
                    status="inferred" if ref["kind"] == "reference" or derivation == "missing_year" else "explicit")
    except (ValueError, OverflowError, TypeError):
        return unresolved("标准时间不合法，请核对日期、时刻和时区。")
