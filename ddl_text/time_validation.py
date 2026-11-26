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

# The model selects the day meant by this action. Calendar arithmetic belongs here.
RELATIVE_DAYS = {"前天": -2, "昨天": -1, "今天": 0, "今日": 0, "今早": 0, "今晚": 0,
                 "明天": 1, "明早": 1, "明晚": 1, "后天": 2, "大后天": 3}
DAY_WORD = re.compile("|".join(sorted(RELATIVE_DAYS, key=len, reverse=True)))


def normalized_time_schema(quick_reference=None):
    anchors = [None, "published_at", "collected_at"]
    if quick_reference:
        anchors.extend(["quick_reference", *quick_reference.get("relative_days", {})])
    properties = {
        "value": {"type": ["string", "null"]},
        "precision": {"type": "string", "enum": ["date", "datetime", "unknown"]},
        "derivation": {"type": "string", "enum": list(DERIVATIONS)},
        "anchor": {"type": ["string", "null"], "enum": anchors},
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
    day_offsets = {RELATIVE_DAYS[word] for word in DAY_WORD.findall(record["text"])}
    return dict(value=local.isoformat(), precision=precision,
                origin="指定的通知时间" if publication["value"] else "加入清单的时间",
                relative_days={word: date.fromordinal(day.toordinal()+offset).isoformat()
                               for word, offset in RELATIVE_DAYS.items()
                               if offset in day_offsets and 1 <= day.toordinal()+offset <= date.max.toordinal()},
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
    anchor = message.get("quick_reference")
    if not anchor or ref["kind"] != "deadline":
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
    closing = _DAY_END.match(tail)
    if not window and not (len(dates) == 1 and closing and
                           (re.match(r"\s*[^，。；、\d]{0,12}(?:关闭|截止|截至|结束)", tail[closing.end():])
                            or re.search(r"(?:截止|截至|最迟)(?:时间)?[为到：:\s]*$", quote[:end.start()]))):
        return n
    # An explicit sub-day limit may come from a second part of the notice.
    if window and n["precision"] == "datetime" and n["value"] and not re.search(
            r"T(?:24:00|00:00|23:59)(?::(?:00|59))?(?:Z|[+-]\d\d:\d\d)?$", n["value"]):
        return n
    if re.search(r"原定|原计划|改为|延期|待定|另行通知|并非|取消|假设|去年|明年|次年", quote):
        return n
    year, month, day = end.groups()
    if window and dates[0][1] and not year:
        # A stated start year carries through the window, including Dec–Jan.
        start = date(*map(int, dates[0].groups()))
        year = start.year + ((int(month), int(day)) < (start.month, start.day))
    if year:
        last = date(int(year), int(month), int(day))
        derivation, field = "absolute", None
    else:
        last = _nearest_year(date(2000, int(month), int(day)), date.fromisoformat(anchor["value"][:10]))
        derivation, field = "missing_year", "quick_reference"
    return dict(n, value=last.isoformat(), precision="date", derivation=derivation, anchor=field)


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
    require(n["anchor"] in (None, "published_at", "collected_at", "quick_reference", *RELATIVE_DAYS))
    require(nonempty(n["basis"]))
    zone = _zone(message.get("timezone"))
    result = {"value": None, "precision": "unknown", "timezone": zone.key if zone else None,
              "kind": ref["kind"], "status": "unknown", "raw": ref["quote"],
              "basis": n["basis"], "alternatives": []}

    def unresolved(reason):
        return dict(result, basis=reason)

    try:
        n = _inclusive_day(ref, message)
    except (ValueError, OverflowError):
        return unresolved("原文中的办理日期无效。")
    if n["derivation"] == "unresolved" or ref["kind"] == "urgency":
        if n["value"] is not None or n["precision"] != "unknown" or n["anchor"] is not None:
            return unresolved("未确定的时间不能同时包含确定日期或计算基准。")
        return result
    if n["value"] is None or n["precision"] == "unknown":
        return unresolved("时间归一化缺少具体值或精度。")
    try:
        time_value(n["value"], n["precision"])
        word = n["anchor"]
        if word in RELATIVE_DAYS:
            calendar = (message.get("quick_reference") or {}).get("relative_days", {})
            cited_days = {RELATIVE_DAYS[token] for token in DAY_WORD.findall(ref["quote"])}
            if n["derivation"] != "relative" or word not in calendar or RELATIVE_DAYS[word] not in cited_days:
                return unresolved("相对日期与引用原话或录入基准不一致。")
            n = dict(n, value=calendar[word] + n["value"][10:], anchor="quick_reference")
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
