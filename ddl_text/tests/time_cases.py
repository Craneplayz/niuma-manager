"""Synthetic semantic smoke cases; expected values are hand-authored."""

from ddl_text.contracts import normalize_record


UNKNOWN_PUBLICATION = {"value": None, "precision": "unknown", "basis": "unknown"}


def case(name, text, expected, **metadata):
    record = normalize_record(dict({
        "input_id": "synthetic:time:" + name, "text": text, "text_quality": "complete",
        "published_at": {"value": "2026-09-29T10:00:00+08:00", "precision": "datetime", "basis": "source"},
        "collected_at": "2026-09-29T10:05:00+08:00", "timezone": "Asia/Shanghai",
        "timezone_basis": "source", "course_or_project": "合成测试课程"}, **metadata), "0.2")
    return {"name": name, "request": {"contract_version": "0.2", "request_id": "smoke:" + name, "records": [record]},
            "expected": expected}


def deadline(value, status="explicit", kind="deadline", notice="create", alternatives=()):
    return {"value": value, "status": status, "kind": kind, "notice_type": notice,
            "precision": "datetime" if value and "T" in value else "date" if value else "unknown",
            "alternatives": sorted(alternatives)}


CASES = [
    case("parentheses", "请于2026.10.11（23:59:00）前提交作业。", [deadline("2026-10-11T23:59:00+08:00")]),
    case("chinese-numerals", "课程回顾须在二〇二六年十月十三日晚上九点一刻之前交齐。", [deadline("2026-10-13T21:15:00+08:00")]),
    case("english", "Please submit the report by 30 September 2026, 6:15 pm.", [deadline("2026-09-30T18:15:00+08:00")]),
    case("dispersed", "课程报告的提交日期是2026年10月12日。当日最晚下午三点半上传。", [deadline("2026-10-12T15:30:00+08:00")]),
    case("urgency", "请于2026.9.29 9:00前交一份课程内容回顾，内容范围为目前学过的全部内容（包括当次课），请大家尽快完成，会按比例计入总评。", [deadline("2026-09-29T09:00:00+08:00")]),
    case("seconds", "请在2026/10/11 (23：59：37)之前提交报告。", [deadline("2026-10-11T23:59:37+08:00")]),
    case("missing-year", "请于十月一日中午十二点前填好问卷。", [deadline("2026-10-01T12:00:00+08:00", "inferred")]),
    case("collection-year", "请于9/30前上传报告。", [deadline("2026-09-30", "inferred")], published_at=UNKNOWN_PUBLICATION),
    case("cross-year", "请下个月三号中午十二点前提交课程回顾。", [deadline("2027-01-03T12:00:00+08:00")],
         published_at={"value": "2026-12-31T10:00:00+08:00", "precision": "datetime", "basis": "source"}),
    case("hour-offset", "请在本通知发布后的三十六小时内提交报告。", [deadline("2026-09-30T22:00:00+08:00")]),
    case("month-end", "本月底之前提交课程报告。", [deadline("2026-09-30")]),
    case("no-anchor", "请明天18点前交报告。", [deadline(None, "unknown")], published_at=UNKNOWN_PUBLICATION),
    case("reference", "2026-9-29 9:00~12:00有一场随堂小测，请提前认真复习。", [deadline("2026-09-29T09:00:00+08:00", "inferred", "reference")]),
    case("conflict", "报告的截止日期尚未确认，一处写2026年10月11日，另一处写2026年10月13日；请准备提交。", [deadline(None, "conflict", alternatives=("2026-10-11", "2026-10-13"))]),
    case("postpone", "报告原定2026年10月11日提交，现延期至2026年10月13日。", [deadline("2026-10-13", notice="postpone")]),
    case("invalid-date", "请于2026年2月30日交报告。", [deadline(None, "unknown")]),
    case("no-date", "请提交课程报告。", [deadline(None, "not_provided", "unknown")]),
    case("no-task", "谢谢大家，昨天所有报告都已经提交完毕。", []),
    case("missing-calendar", "请于学校秋假后的第一个工作日提交报告。", [deadline(None, "unknown")]),
    case("context-anchor", "补充要求：实验报告需附代码，截止日期仍按原通知。", [deadline("2024-10-01", notice="update")], context=[
        {"input_id": "synthetic:old", "text": "请后天交实验报告。",
         "published_at": {"value": "2024-09-29", "precision": "date", "basis": "source"},
         "timezone": "Asia/Shanghai", "platform": "unknown", "source_url": None, "source_label": "合成原通知"}]),
]
