"""Fixed synthetic F3 cases and human-authored routing expectations."""

from ddl_text.contracts import normalize_record


UNKNOWN_PUBLICATION = {"value": None, "precision": "unknown", "basis": "unknown"}


def case(name, text, expected, route="tasks", expected_alternatives=(), **metadata):
    record = normalize_record(dict({
        "input_id": "synthetic:f3:" + name, "text": text, "platform": "teaching_web",
        "published_at": {"value": "2026-09-30T10:00:00+08:00", "precision": "datetime", "basis": "source"},
        "collected_at": "2026-09-30T10:05:00+08:00", "timezone": "Asia/Shanghai",
        "timezone_basis": "source", "course_or_project": "合成分类课程"}, **metadata), "0.2")
    return {"name": name, "request": {"contract_version": "0.2", "request_id": "f3:" + name, "records": [record]},
            "expected": expected, "expected_alternatives": list(expected_alternatives), "route": route}


def task(value=None, notice="create", review=False, status=None):
    return {"notice_type": notice, "value": value, "needs_review": review,
            "status": status or ("explicit" if value else "not_provided")}


CASES = [
    case("dated", "请于2026年10月1日18点前提交课程报告，请大家尽快完成。", [task("2026-10-01T18:00:00+08:00")]),
    case("undated", "请所有同学加入课程微信群。", [task()]),
    case("information", "昨日学院举办学术交流活动，师生参与踊跃，活动圆满结束。", [], "information"),
    case("postpone", "课程报告原定2026年10月1日提交，现延期至2026年10月5日。", [task("2026-10-05", "postpone", True)]),
    case("cancel", "取消本次课程报告提交，大家不需要再交本次课程报告。", [task(notice="cancel", review=True)]),
    case("old-yearless", "请于10月1日前提交课程报告。", [], "filtered_history",
         published_at={"value": "2024-09-30", "precision": "date", "basis": "source"}),
    case("archived-undated", "2023秋季学期已结束。以下为该学期归档的旧通知：请加入本课程微信群。", [], "filtered_history",
         published_at=UNKNOWN_PUBLICATION),
    case("unknown-age", "请于10月1日前提交课程报告。", [task("2026-10-01", review=True)],
         published_at=UNKNOWN_PUBLICATION),
    case("yearless-current", "请于10月1日前提交课程报告。", [task("2026-10-01")]),
    case("old-but-future", "请于2027年1月1日前提交项目报告。", [task("2027-01-01")],
         published_at={"value": "2024-09-30", "precision": "date", "basis": "source"}),
    case("ongoing", "2024年10月1日是课程报告原截止。至今未交的同学仍须补交，新的补交期限尚未确定。",
         [task(review=True, status="unknown")],
         # Without a user's task list this may be either a new make-up action or
         # an update to the original report. Both must stay reviewable and undated.
         expected_alternatives=[[task(notice="update", review=True, status="unknown")]]),
    case("recent-overdue", "请于2026年9月29日前提交课程报告。", [task("2026-09-29", review=True)]),
    case("mixed", "上学期已结束，归档旧要求：加入上学期课程群。新学期请于2026年10月5日前填写选课问卷。",
         [task("2026-10-05")], "mixed"),
    case("optional", "有意参加讲座的同学可于2026年10月5日前报名，是否参加自行决定。", [task("2026-10-05", review=True)]),
]


def signature(candidate):
    return {"notice_type": candidate["notice_type"], "value": candidate["deadline"]["value"],
            "status": candidate["deadline"]["status"], "needs_review": candidate["needs_review"]}


def matches(case, result):
    actual = sorted(map(signature, result["tasks"]), key=repr)
    choices = [case["expected"]] + case.get("expected_alternatives", [])
    if result["errors"] or not any(actual == sorted(expected, key=repr) for expected in choices):
        return False
    warnings = result["record_results"][0]["warnings"]
    historical = any(w.startswith("history_filtered:") for w in warnings)
    if case["route"] in {"filtered_history", "mixed"}:
        return historical
    if case["route"] == "information":
        return any(w.startswith("no_action:") for w in warnings) and not historical
    return not historical
