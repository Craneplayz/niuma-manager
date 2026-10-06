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
    windows = []
    day = requested.date()
    while day <= final_date:
        if (day.weekday() + 1) % 7 in weekdays:
            start = datetime.combine(day, start_clock, requested.tzinfo)
            end = datetime.combine(day, end_clock, requested.tzinfo)
            start = max(start, requested)
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


def build_proposal(request, estimates):
    """Build a contract-compatible proposal without writing tasks or calendars."""
    requested = datetime.fromisoformat(request["requestedAt"])
    ranked, free = rank_tasks(request, estimates)
    blocks, warnings = [], []
    due_ids = {item["task"]["id"] for item in ranked}
    for task in request.get("tasks", []):
        if task.get("id") not in due_ids:
            warnings.append(f"{task.get('title', task.get('id', '任务'))}：没有 DDL，未自动排程")

    for item in ranked:
        remaining = item["estimate"]
        preferred = _preferred_start(item, requested)
        eligible = [i for i, (start, _) in enumerate(free) if preferred <= start < item["due"]]
        fallback = [i for i, (start, _) in enumerate(free) if start < preferred and start < item["due"]]
        order = eligible + list(reversed(fallback))
        while remaining > 0 and order:
            index = order.pop(0)
            if index >= len(free):
                continue
            start, end = free[index]
            if start >= item["due"]:
                continue
            available = (min(end, item["due"]) - start).total_seconds() / 60
            if available <= 0:
                continue
            if MAX_BLOCK_MINUTES < remaining <= 120:
                target = remaining / 2
            else:
                target = min(MAX_BLOCK_MINUTES, remaining)
            duration = min(target, available)
            block_start, block_end = _take(free, index, duration)
            # Removing or shortening a free interval changes later indexes.
            order = [i - 1 if block_end == end and i > index else i for i in order if i != index]
            title = item["task"].get("title") or item["task"]["id"]
            blocks.append({"taskId": item["task"]["id"], "title": title,
                           "startAt": block_start.isoformat(), "endAt": block_end.isoformat()})
            remaining -= duration
            # Rebuild indexes because free was mutated and new space may remain at the same index.
            eligible = [i for i, (left, _) in enumerate(free)
                        if preferred <= left < item["due"]]
            fallback = [i for i, (left, _) in enumerate(free)
                        if left < preferred and left < item["due"]]
            order = eligible + list(reversed(fallback))
        if remaining > 0.01:
            warnings.append(f"{item['task'].get('title', item['task']['id'])}：P{item['level']}，DDL 前尚缺 {math.ceil(remaining)} 分钟")

    blocks.sort(key=lambda block: block["startAt"])
    parts = {}
    for block in blocks:
        task_id = block["taskId"]
        if estimates[task_id] > MAX_BLOCK_MINUTES:
            parts[task_id] = parts.get(task_id, 0) + 1
            block["title"] = f"{block['title']}（第 {parts[task_id]} 段）"
    return {"estimates": dict(estimates), "blocks": blocks, "warnings": warnings}
