"""Validate model-normalized dates against their source-local time context.

Evidence membership is checked by extraction._reference. These checks establish
structural consistency, not that a model understood a sentence correctly.
"""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import nonempty, require
from .contract_v02 import time_value


NORMALIZED_FIELDS = {"value", "precision", "derivation", "anchor", "basis"}
DERIVATIONS = ("absolute", "relative", "offset", "missing_year", "unresolved")


def normalized_time_schema(*, quick_entry=False):
    properties = {
        "value": {"type": ["string", "null"]},
        "precision": {"type": "string", "enum": ["date", "datetime", "unknown"]},
        "derivation": {"type": "string", "enum": list(DERIVATIONS)},
        "anchor": {"type": ["string", "null"], "enum": [None, "published_at", "collected_at"] + (["quick_reference"] if quick_entry else [])},
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


def quick_reference(record):
    """Account-scoped manual Add only; the original publication stays untouched."""
    publication = record["published_at"]
    if record["platform"] != "manual" or (record["source_url"] and not publication["value"]):
        return None
    value = publication["value"] or record.get("collected_at")
    if not value or not record.get("timezone"):
        return None
    precision = publication["precision"] if publication["value"] else "datetime"
    zone = _zone(record["timezone"])
    if zone is None:
        return None
    try:
        local = (date.fromisoformat(value) if precision == "date" else
                 datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(zone))
    except OverflowError:
        return None
    day = local.date() if isinstance(local, datetime) else local
    monday = day - timedelta(days=day.weekday())
    return dict(value=local.isoformat(), precision=precision,
                origin="指定的通知时间" if publication["value"] else "加入清单的时间",
                week_start=monday.isoformat(),
                weeks={name: [date.fromordinal(ordinal).isoformat() if 1 <= ordinal <= date.max.toordinal() else None
                              for ordinal in range(monday.toordinal() + offset, monday.toordinal() + offset + 7)]
                       for name, offset in (("上周", -7), ("本周", 0), ("下周", 7))})


def _nearest_year(day, anchor):
    choices = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            choices.append(day.replace(year=year))
        except ValueError:
            pass
    return min(choices, key=lambda candidate: (abs((candidate - anchor).days), candidate < anchor))


_CALENDAR_DAY = re.compile(r"(?<!\d)(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_DAY_END = re.compile(r"\s*24\s*(?:[:：]00(?:[:：]00)?|[时点](?:\s*0{1,2}\s*分)?)(?![\s\d:：]*\d)")


def _inclusive_day(ref, message):
    """Recognize equivalent end-of-day proposals for a single cited deadline."""
    n = ref["normalized"]
    if not message.get("quick_reference") or ref["kind"] != "deadline" or not n["value"]:
        return n
    # Limit the repair to day boundaries. A separately specified hour wins.
    stamp = re.fullmatch(r"(\d{4}-\d\d-\d\d)T(24:00|00:00|23:59)(?::(\d\d))?(?:Z|[+-]\d\d:\d\d)?", n["value"])
    if not stamp or (stamp[3] not in (None, "00") and (stamp[2], stamp[3]) != ("23:59", "59")):
        return n
    quote = ref["quote"].replace("**", "")
    dates = list(_CALENDAR_DAY.finditer(quote))
    if not dates:
        return n
    end = dates[-1]
    tail = quote[end.end():]
    window = (len(dates) == 2 and quote[dates[0].end():end.start()].strip() in ("至", "到", "—", "–", "-")
              and re.match(r"\s*期间(?:内)?\s*(?:完成|办理|提交|申请|报名)", tail)
              and not re.search(r"\d\s*(?:[:：点时]|分)", tail))
    if not window and not (len(dates) == 1 and _DAY_END.match(tail)):
        return n
    if n["derivation"] not in ("absolute", "missing_year"):
        return n
    day = date.fromisoformat(stamp[1])
    if stamp[2] == "00:00" and (day.month, day.day) != (int(end[2]), int(end[3])):
        day -= timedelta(days=1)
    if (day.month, day.day) != (int(end[2]), int(end[3])) or end[1] and day.year != int(end[1]):
        return n
    # Keep the existing year/anchor checks. Model variants then compare equal
    # when separate evidence spans describe the window and the closing time.
    return dict(n, value=day.isoformat(), precision="date")


def validate_time(ref, message):
    """Keep the action reviewable when a well-shaped date proposal is invalid.

    Missing fields/types remain protocol errors. Derivation and role are
    semantic judgments supplied by the model.
    """
    require(ref["kind"] in {"deadline", "reference", "urgency"})
    n = ref["normalized"]
    require(isinstance(n, dict) and set(n) == NORMALIZED_FIELDS)
    require(n["value"] is None or isinstance(n["value"], str))
    require(n["precision"] in {"date", "datetime", "unknown"})
    require(n["derivation"] in DERIVATIONS)
    require(n["anchor"] in (None, "published_at", "collected_at", "quick_reference"))
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
        n = _inclusive_day(ref, message)
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
        elif field == "quick_reference":
            anchor = message.get("quick_reference")
            if not anchor or (derivation == "offset" and anchor["precision"] != "datetime"):
                return unresolved("此条原文没有可用的快捷录入日期基准。")
            anchor_day = date.fromisoformat(anchor["value"][:10])
            if derivation == "missing_year" and day != _nearest_year(day, anchor_day):
                return unresolved("补全的年份不符合最近有效日期规则。")
            basis = "按%s %s 换算。" % (anchor["origin"], anchor["value"])
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
                    # A validated nearest-year completion is accepted by policy;
                    # raw and basis still record that the year was omitted.
                    status="inferred" if ref["kind"] == "reference" else "explicit")
    except (ValueError, OverflowError, TypeError):
        return unresolved("标准时间不合法，请核对日期、时刻和时区。")
