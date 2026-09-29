"""Evidence-based dates with explicit, reviewable missing-year inference.

Missing years follow a documented nearest-date policy; unsupported wording stays unknown.
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def unknown(raw, basis, kind="unknown", zone_name=None):
    return {"value": None, "precision": "unknown", "timezone": zone_name,
            "kind": kind, "status": "unknown", "raw": raw,
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


def _nearest_year(month, day, anchor):
    """Choose the nearest calendar date, not an unconditional future rollover."""
    choices = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            choices.append(date(year, month, day))
        except ValueError:
            continue
    if not choices:
        raise ValueError("no valid date in adjacent years")
    return min(choices, key=lambda candidate: (
        abs((candidate - anchor).days), candidate < anchor))


def resolve_time(raw, message, kind):
    zone_name = message.get("timezone")
    def unresolved(basis):
        return unknown(raw, basis, kind, zone_name)

    result = unresolved("时间表达尚不能唯一换算，请查看原文。")
    phrase = re.sub(r"\s+", "", raw)
    # Accept boundary words inside an authentic quotation without editing raw evidence.
    phrase = re.sub(r"^(?:截止至|截至|截止到)", "", phrase)
    phrase = re.sub(r"(?:之前|以前|前|截止|为止)$", "", phrase)
    zone = _zone(zone_name)
    try:
        base = _base(message, zone)
        day = None
        stamp = None
        year_basis = None
        period = ""
        rest = ""
        hours = re.fullmatch(r"([1-9]\d{0,3})小时后", phrase)
        absolute = re.fullmatch(r"(\d{4})[-年/.](\d{1,2})[-月/.](\d{1,2})日?(.*)", phrase)
        month_day = re.fullmatch(r"(今年|明年|去年)?(\d{1,2})月(\d{1,2})[日号]?(.*)", phrase)
        numeric_month_day = re.fullmatch(r"(\d{1,2})[-/\.](\d{1,2})(.*)", phrase)
        relative = re.fullmatch(r"(今天|今日|明天|明日|明晚|后天)(.*)", phrase)
        weekday = re.fullmatch(r"(本周|下周)([一二三四五六日天])(.*)", phrase)
        if hours:
            if not isinstance(base, datetime):
                return unresolved("小时偏移需要可信发布时间、具体时刻和明确时区。")
            stamp = (base.astimezone(timezone.utc) + timedelta(hours=int(hours[1]))).astimezone(zone)
        elif absolute:
            day = date(int(absolute[1]), int(absolute[2]), int(absolute[3]))
            rest = absolute[4]
        elif month_day or numeric_month_day:
            qualifier = month_day[1] if month_day else None
            month, day_number, rest = (month_day[2], month_day[3], month_day[4]) if month_day else numeric_month_day.groups()
            anchor = base
            anchor_label = "发布时间"
            if anchor is None and not qualifier and zone is not None and message.get("collected_at"):
                anchor = datetime.fromisoformat(message["collected_at"].replace("Z", "+00:00")).astimezone(zone)
                anchor_label = "采集时间"
            if anchor is None:
                return unresolved("缺年份日期需要该消息的可信发布时间，或带时区的采集时间作为推断基准。")
            anchor_day = anchor.date() if isinstance(anchor, datetime) else anchor
            if qualifier:
                day = date(anchor_day.year + {"今年": 0, "明年": 1, "去年": -1}[qualifier], int(month), int(day_number))
            else:
                day = _nearest_year(int(month), int(day_number), anchor_day)
                year_basis = f"按{anchor_label} {anchor_day.isoformat()} 补全年份。"
        elif relative or weekday:
            if base is None:
                return unresolved("相对日期缺少可信的原消息发布时间或明确时区；采集时间不能代替。")
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
            # Only unwrap a matched pair around the time; raw remains evidence.
            if (rest[0], rest[-1]) in {("(", ")"), ("（", "）")}:
                rest = rest[1:-1]
            interval = re.fullmatch(r"(\d{1,2}[:：]\d{2})[~～至到–—-](\d{1,2}[:：]\d{2})", rest)
            if interval and kind == "reference":
                start = resolve_time(day.isoformat() + "T" + interval[1], message, kind)
                end = resolve_time(day.isoformat() + "T" + interval[2], message, kind)
                if not start["value"] or not end["value"] or start["value"] >= end["value"]:
                    return unresolved("活动时间区间无效，请核对原文。")
                start.update(raw=raw, basis=(year_basis or "") + "取活动开始时间作为参考，并非提交截止。")
                return start
            clock = re.fullmatch(r"(?:T)?(上午|下午|晚上|晚间|中午)?(\d{1,2})(?:[:：](\d{2})(?:[:：](\d{2}))?|[点时](?:(\d{1,2})分?)?)(?:整)?", rest)
            if not clock or zone is None:
                return unresolved("时刻格式不明确或时区不可用，保留原话待核实。")
            if period and clock[1] and clock[1] not in {"晚上", "晚间"}:
                return unresolved("日期短语与时段相互矛盾，请核实。")
            period = clock[1] or period
            hour, minute, second = int(clock[2]), int(clock[3] or clock[5] or 0), int(clock[4] or 0)
            if period:
                if not 1 <= hour < 12 or period == "中午":
                    return unresolved("时段与时刻含义不能唯一确定。")
                hour = hour % 12 + (12 if period in {"下午", "晚上", "晚间"} else 0)
            stamp = _localize(day, time(hour, minute, second), zone)
        elif period and stamp is None:
            return unresolved("只给出晚间等时段，缺少具体时刻。")
        if zone_name == "Asia/Shanghai" and (stamp.date() if stamp else day) < date(1992, 1, 1):
            return unresolved("该历史日期需要另行核对时区规则。")
        value = stamp.isoformat(timespec="seconds") if stamp else day.isoformat()
        return {"value": value, "precision": "datetime" if stamp else "date",
                "timezone": zone_name if zone is not None else None,
                "kind": kind, "status": "inferred" if kind == "reference" or year_basis else "explicit",
                "raw": raw, "basis": ((year_basis + (" 引用的是活动参考时间，不是明确截止时间。" if kind == "reference" else "")) if year_basis else
                                       ("引用活动时间作为参考完成时间，需核实。" if kind == "reference"
                                        else "按原文日期及该条消息可信的发布时间换算；保留原始精度。")),
                "alternatives": []}
    except (ValueError, OverflowError, TypeError):
        return unresolved("日期或时刻无效，或处于时区歧义范围，请查看原文。")


def combine_times(items, default_zone=None):
    if not items:
        return unknown(None, "原文未给出可确定的完成日期。", zone_name=default_zone)
    # Bare urgency cues do not override concrete deadlines; other unresolved
    # expressions and genuinely conflicting dates still require review.
    if any(item["value"] is not None and item["kind"] == "deadline" for item in items):
        items = [item for item in items if not (
            item["value"] is None and item["kind"] == "deadline" and
            re.sub(r"\s+", "", item["raw"] or "") in
            {"尽快", "尽早", "抓紧", "尽快完成", "尽早完成"})]
    kinds = {item["kind"] for item in items}
    zones = {item["timezone"] for item in items}
    kind = items[0]["kind"] if len(kinds) == 1 else "unknown"
    zone_name = items[0]["timezone"] if len(zones) == 1 else None
    if any(item["value"] is None for item in items):
        return unknown("；".join(item["raw"] for item in items),
                       "；".join(dict.fromkeys(item["basis"] for item in items if item["value"] is None)), kind, zone_name)
    if len({item["kind"] for item in items}) > 1:
        return unknown("；".join(item["raw"] for item in items), "同时引用了截止和参考时间，需要核实各自用途。", zone_name=zone_name)
    distinct = list(dict.fromkeys(item["value"] for item in items))
    if len(distinct) > 1:
        result = unknown("；".join(item["raw"] for item in items), "同一任务存在多个未消解的时间，请核实。", kind, zone_name)
        result.update(status="conflict", alternatives=distinct)
        return result
    return items[0]
