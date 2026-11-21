"""Deterministic priority and time-block scheduling for planning proposals."""

from datetime import datetime, time, timedelta
import math


MAX_HORIZON_DAYS = 366
MAX_BLOCK_MINUTES = 90


def _due_at(task, tz):
    due = task.get("due")
    if not isinstance(due, dict) or not due.get("date"):
        return None
    clock = due.get("time") or "23:59:59"
    value = datetime.fromisoformat(f"{due['date']}T{clock}")
    return value.replace(tzinfo=tz)


def minutes_to_schedule(task, requested, estimate):
    """Credit only identifiable, locked study time still ahead of this task's deadline."""
    deadline = _due_at(task, requested.tzinfo)
    if not deadline:
        return estimate
    booked = []
    for plan in task.get("plans", []):
        if not plan.get("isLocked") or not plan.get("endAt"):
            continue
        start = max(requested, datetime.fromisoformat(plan["startAt"]))
        end = min(deadline, datetime.fromisoformat(plan["endAt"]))
        if start < end:
            booked.append((start, end))
    credited = 0
    covered_until = requested
    for start, end in sorted(booked):
        credited += max(0, (end - max(start, covered_until)).total_seconds() / 60)
        covered_until = max(covered_until, end)
    return max(0, estimate - credited)


def _clock(value):
    return time.fromisoformat(value)


def _subtract(intervals, occupied):
    free = []
    for start, end in intervals:
        parts = [(start, end)]
        for busy_start, busy_end in occupied:
            next_parts = []
            for left, right in parts:
                if busy_end <= left or right <= busy_start:
                    next_parts.append((left, right))
                else:
                    if left < busy_start:
                        next_parts.append((left, busy_start))
                    if busy_end < right:
                        next_parts.append((busy_end, right))
            parts = next_parts
        free.extend(parts)
    return [(start, end) for start, end in free if start < end]


def _free_windows(request, latest_due):
    requested = datetime.fromisoformat(request["requestedAt"])
    if requested.tzinfo is None:
        raise ValueError("requestedAt must include a timezone")
    availability = request["availability"]
    start_clock, end_clock = _clock(availability["startTime"]), _clock(availability["endTime"])
    weekdays = set(availability["weekdays"])
    final_date = min(latest_due.date(), requested.date() + timedelta(days=MAX_HORIZON_DAYS))
    # A proposal whose first block starts at the request timestamp is already
    # expired by the time the student can review it. Use the next five-minute
    # boundary with at least one minute for generating and confirming a preview.
    earliest = (requested + timedelta(minutes=1)).replace(second=0, microsecond=0)
    if earliest < requested + timedelta(minutes=1):
        earliest += timedelta(minutes=1)
    earliest += timedelta(minutes=(-earliest.minute) % 5)
    windows = []
    day = requested.date()
    while day <= final_date:
        if (day.weekday() + 1) % 7 in weekdays:
            start = datetime.combine(day, start_clock, requested.tzinfo)
            end = datetime.combine(day, end_clock, requested.tzinfo)
            start = max(start, earliest)
            if start < end:
                windows.append((start, end))
        day += timedelta(days=1)
    occupied = []
    for plan in request.get("existingPlans", []):
        start = datetime.fromisoformat(plan["startAt"])
        end = datetime.fromisoformat(plan["endAt"]) if plan.get("endAt") else start + timedelta(hours=1)
        occupied.append((start, end))
    return _subtract(windows, occupied)


def _minutes_before(windows, due):
    return sum(max(0, (min(end, due) - start).total_seconds() / 60)
               for start, end in windows if start < due)


def _level(slack, estimate):
    if slack < 0:
        return 0
    if slack < max(60, estimate * 0.5):
        return 1
    if slack < estimate * 2:
        return 2
    return 3


def rank_tasks(request, estimates):
    """Return schedulable tasks in risk order with explainable priority data."""
    requested = datetime.fromisoformat(request["requestedAt"])
    tasks = request.get("tasks", [])
    due_values = [_due_at(task, requested.tzinfo) for task in tasks]
    latest_due = max((due for due in due_values if due), default=requested)
    windows = _free_windows(request, latest_due)
    user_priority = {"high": 0, "medium": 1, "low": 2}
    ranked = []
    for index, task in enumerate(tasks):
        task_id = task.get("id")
        estimate = estimates.get(task_id)
        if type(estimate) not in (int, float) or not math.isfinite(estimate) or estimate <= 0:
            raise ValueError("every task requires a positive estimate")
        due = due_values[index]
        if due is None:
            continue
        estimate = minutes_to_schedule(task, requested, estimate)
        capacity = _minutes_before(windows, due)
        slack = capacity - estimate
        ranked.append({"task": task, "estimate": float(estimate), "due": due,
                       "capacity": capacity, "slack": slack,
                       "level": _level(slack, estimate), "index": index,
                       "user_priority": user_priority.get(task.get("priority"), 1)})
    ranked.sort(key=lambda item: (item["level"], item["due"], item["user_priority"], item["index"]))
    return ranked, windows


def _preferred_start(item, requested):
    task = item["task"]
    if item["estimate"] <= 90 and len(task.get("subtasks", [])) <= 1:
        lead = timedelta(hours=24)
    elif item["estimate"] <= 240 and len(task.get("subtasks", [])) <= 2:
        lead = timedelta(hours=72)
    else:
        lead = timedelta(hours=96)
    return max(requested, item["due"] - lead)


def _take(free, index, minutes):
    start, end = free[index]
    finish = min(end, start + timedelta(minutes=minutes))
    if finish == end:
        free.pop(index)
    else:
        free[index] = (finish, end)
    return start, finish


def _allocate(ranked, windows, requested, *, prefer_study_days):
    free = windows.copy()
    blocks, shortfalls = [], {}
    for item in ranked:
        task = item["task"]
        remaining = item["estimate"]
        preferred = _preferred_start(item, requested) if prefer_study_days else requested
        while remaining > 0.01:
            # Choose the first window in the preferred period, otherwise the
            # latest available study day before it. Occupied time was removed.
            fallback = None
            index = None
            for i, (start, end) in enumerate(free):
                if start >= item["due"]:
                    break
                fallback = i
                if start >= preferred:
                    index = i
                    break
            if index is None:
                index = fallback
            if index is None:
                shortfalls[task["id"]] = remaining
                break
            start, end = free[index]
            available = (min(end, item["due"]) - start).total_seconds() / 60
            target = remaining / 2 if MAX_BLOCK_MINUTES < remaining <= 120 else min(MAX_BLOCK_MINUTES, remaining)
            duration = min(target, available)
            start, end = _take(free, index, duration)
            blocks.append({"taskId": task["id"], "title": task.get("title") or task["id"],
                           "startAt": start.isoformat(), "endAt": end.isoformat()})
            remaining -= duration
    return blocks, shortfalls


def build_proposal(request, estimates):
    """Build a contract-compatible proposal without writing tasks or calendars."""
    requested = datetime.fromisoformat(request["requestedAt"])
    ranked, free = rank_tasks(request, estimates)
    warnings = []
    due_ids = {item["task"]["id"] for item in ranked}
    for task in request.get("tasks", []):
        if task.get("id") not in due_ids:
            warnings.append(f"{task.get('title', task.get('id', '任务'))}：没有 DDL，未自动排程")

    blocks, shortfalls = _allocate(ranked, free, requested, prefer_study_days=True)
    if shortfalls and len(ranked) > 1:
        # Filling a later assignment first can strand an earlier one even when
        # there is enough time for both. Earliest-deadline order recovers that
        # capacity without moving any class, exam, or previously booked block.
        by_deadline = sorted(ranked, key=lambda item: item["due"])
        alternative, missing = _allocate(by_deadline, free, requested, prefer_study_days=False)
        if sum(missing.values()) < sum(shortfalls.values()):
            blocks, shortfalls = alternative, missing
    for item in ranked:
        task = item["task"]
        if item["estimate"] == 0:
            warnings.append(f"{task.get('title', task['id'])}：已锁定的学习安排已覆盖预计工时")
        elif task["id"] in shortfalls:
            warnings.append(f"{task.get('title', task['id'])}：P{item['level']}，DDL 前尚缺 {math.ceil(shortfalls[task['id']])} 分钟")

    blocks.sort(key=lambda block: block["startAt"])
    parts = {}
    for block in blocks:
        task_id = block["taskId"]
        if estimates[task_id] > MAX_BLOCK_MINUTES:
            parts[task_id] = parts.get(task_id, 0) + 1
            block["title"] = f"{block['title']}（第 {parts[task_id]} 段）"
    return {"estimates": dict(estimates), "blocks": blocks, "warnings": warnings}
