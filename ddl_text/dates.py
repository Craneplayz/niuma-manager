"""Conservative date conversion using the quoted message's own publication.

Unsupported wording is a reviewable unknown, never a best-effort guessed date.
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def unknown(raw, basis):
    return {"value": None, "precision": "unknown", "timezone": None,
            "kind": "unknown", "status": "unknown", "raw": raw,
            "basis": basis, "alternatives": []}


def _zone(name):
    # Modern Shanghai dates work on Windows without an optional tzdata package.
    # Historical DST dates are deliberately not converted via this fallback.
    if name in {"Asia/Shanghai", "UTC", "Etc/UTC"}:
        return timezone(timedelta(hours=8 if name == "Asia/Shanghai" else 0))
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _base(message, zone):
    publication = message["published_at"]
    if publication["basis"] != "source" or publication["precision"] == "unknown" or zone is None:
        return None
    if publication["precision"] == "date":
        return date.fromisoformat(publication["value"])
    return datetime.fromisoformat(publication["value"].replace("Z", "+00:00")).astimezone(zone)


def _localize(day, clock, zone):
    naive = datetime.combine(day, clock)
    possibilities = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
            if not any(p.utcoffset() == aware.utcoffset() for p in possibilities):
                possibilities.append(aware)
    if len(possibilities) != 1:
        raise ValueError("ambiguous or nonexistent local time")
    return possibilities[0]


def resolve_time(raw, message, kind):
    result = unknown(raw, "时间表达尚不能唯一换算，请查看原文。")
    phrase = re.sub(r"\s+", "", raw)
    zone_name = message.get("timezone")
    zone = _zone(zone_name)
    try:
        base = _base(message, zone)
        day = None
        stamp = None
        period = ""
        rest = ""
        hours = re.fullmatch(r"([1-9]\d{0,3})小时后", phrase)
        absolute = re.fullmatch(r"(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?(.*)", phrase)
        relative = re.fullmatch(r"(今天|今日|明天|明日|明晚|后天)(.*)", phrase)
        weekday = re.fullmatch(r"(本周|下周)([一二三四五六日天])(.*)", phrase)
        if hours:
            if not isinstance(base, datetime):
                return unknown(raw, "小时偏移需要可信发布时间、具体时刻和明确时区。")
            stamp = (base.astimezone(timezone.utc) + timedelta(hours=int(hours[1]))).astimezone(zone)
        elif absolute:
            day = date(int(absolute[1]), int(absolute[2]), int(absolute[3]))
            rest = absolute[4]
        elif relative or weekday:
            if base is None:
                return unknown(raw, "相对日期缺少可信的原消息发布时间或明确时区；采集时间不能代替。")
            base_day = base.date() if isinstance(base, datetime) else base
            if relative:
                day = base_day + timedelta(days={"今天": 0, "今日": 0, "明天": 1,
                                                "明日": 1, "明晚": 1, "后天": 2}[relative[1]])
                period = "晚上" if relative[1] == "明晚" else ""
                rest = relative[2]
            else:
                index = "一二三四五六日".index(weekday[2].replace("天", "日"))
                day = base_day + timedelta(days=index - base_day.weekday() + (7 if weekday[1] == "下周" else 0))
                rest = weekday[3]
        else:
            return result
        if day is not None and rest:
            clock = re.fullmatch(r"(?:T)?(上午|下午|晚上|晚间|中午)?(\d{1,2})(?:[:：](\d{2})|[点时](?:(\d{1,2})分?)?)(?:整)?", rest)
            if not clock or zone is None:
                return unknown(raw, "时刻格式不明确或时区不可用，保留原话待核实。")
            period = clock[1] or period
            hour, minute = int(clock[2]), int(clock[3] or clock[4] or 0)
            if period:
                if not 1 <= hour <= 12 or period == "中午":
                    return unknown(raw, "时段与时刻含义不能唯一确定。")
                hour = hour % 12 + (12 if period in {"下午", "晚上", "晚间"} else 0)
            stamp = _localize(day, time(hour, minute), zone)
        elif period and stamp is None:
            return unknown(raw, "只给出晚间等时段，缺少具体时刻。")
        if zone_name == "Asia/Shanghai" and (stamp.date() if stamp else day) < date(1992, 1, 1):
            return unknown(raw, "该历史日期需要另行核对时区规则。")
        value = stamp.isoformat(timespec="seconds") if stamp else day.isoformat()
        return {"value": value, "precision": "datetime" if stamp else "date",
                "timezone": zone_name if zone is not None else None,
                "kind": kind, "status": "inferred" if kind == "reference" else "explicit",
                "raw": raw, "basis": ("引用活动时间作为参考完成时间，需核实。" if kind == "reference"
                                        else "按原文日期及该条消息可信的发布时间换算；保留原始精度。"),
                "alternatives": []}
    except (ValueError, OverflowError, TypeError):
        return unknown(raw, "日期或时刻无效，或处于时区歧义范围，请查看原文。")


def combine_times(items):
    if not items:
        return unknown(None, "原文未给出可确定的完成日期。")
    if any(item["value"] is None for item in items):
        return unknown("；".join(item["raw"] for item in items),
                       "；".join(dict.fromkeys(item["basis"] for item in items if item["value"] is None)))
    if len({item["kind"] for item in items}) > 1:
        return unknown("；".join(item["raw"] for item in items), "同时引用了截止和参考时间，需要核实各自用途。")
    distinct = list(dict.fromkeys(item["value"] for item in items))
    if len(distinct) > 1:
        result = unknown("；".join(item["raw"] for item in items), "同一任务存在多个未消解的时间，请核实。")
        result.update(status="conflict", kind="deadline" if items[0]["kind"] == "deadline" else "unknown",
                      alternatives=distinct)
        return result
    return items[0]
