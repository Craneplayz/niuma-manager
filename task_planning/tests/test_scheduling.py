import unittest
from datetime import datetime

from task_planning.scheduling import build_proposal, rank_tasks


def request(tasks, *, requested="2026-10-01T18:00:00+08:00", existing=None,
            start="19:00", end="22:00", weekdays=None):
    return {"schemaVersion": "2.0", "requestedAt": requested, "timezone": "Asia/Shanghai",
            "tasks": tasks, "existingPlans": existing or [],
            "availability": {"startTime": start, "endTime": end,
                             "weekdays": list(range(7)) if weekdays is None else weekdays}}


def task(task_id, title, date=None, clock=None, priority="medium", subtasks=None):
    due = None if date is None else {"date": date, "precision": "datetime" if clock else "date"}
    if due is not None and clock:
        due["time"] = clock
    return {"id": task_id, "title": title, "description": "", "status": "not_started",
            "priority": priority, "due": due, "subtasks": subtasks or [], "plans": []}


class SchedulingTest(unittest.TestCase):
    def test_segments_are_numbered_chronologically_after_fallback(self):
        body = request([task("one", "Assignment", "2026-10-11", "23:59")],
                       requested="2026-10-04T10:00:00+08:00", weekdays=[1, 2, 3, 4, 5])
        proposal = build_proposal(body, {"one": 240})
        blocks = proposal["blocks"]
        self.assertEqual([block["title"] for block in blocks],
                         [f"Assignment（第 {part} 段）" for part in range(1, 4)])
        self.assertEqual([block["startAt"] for block in blocks],
                         ["2026-10-08T19:00:00+08:00", "2026-10-09T19:00:00+08:00",
                          "2026-10-09T20:30:00+08:00"])
        self.assertEqual(self.minutes(proposal, "one"), 240)
        for earlier, later in zip(blocks, blocks[1:]):
            self.assertLessEqual(datetime.fromisoformat(earlier["endAt"]),
                                 datetime.fromisoformat(later["startAt"]))
        self.assertLessEqual(datetime.fromisoformat(blocks[-1]["endAt"]),
                             datetime.fromisoformat("2026-10-11T23:59:00+08:00"))

    def test_segment_numbers_are_independent_for_each_task(self):
        body = request([task("one", "First", "2026-10-11", "23:59"),
                        task("two", "Second", "2026-10-12", "23:59")])
        proposal = build_proposal(body, {"one": 240, "two": 180})
        for task_id, title in [("one", "First"), ("two", "Second")]:
            blocks = [block for block in proposal["blocks"] if block["taskId"] == task_id]
            self.assertEqual([block["title"] for block in blocks],
                             [f"{title}（第 {part} 段）" for part in range(1, len(blocks) + 1)])
            self.assertEqual(self.minutes(proposal, task_id), 240 if task_id == "one" else 180)

    def test_ranks_smaller_deadline_capacity_first(self):
        body = request([task("later", "Later report", "2026-10-04"),
                        task("soon", "Soon quiz", "2026-10-02", "21:00")])
        ranked, _ = rank_tasks(body, {"later": 240, "soon": 120})
        self.assertEqual([item["task"]["id"] for item in ranked], ["soon", "later"])
        self.assertLessEqual(ranked[0]["level"], ranked[1]["level"])

    def test_collision_gives_earlier_deadline_first_claim_on_time(self):
        body = request([task("first", "First", "2026-10-01", "21:00"),
                        task("second", "Second", "2026-10-01", "22:00")])
        proposal = build_proposal(body, {"first": 120, "second": 120})
        first_minutes = self.minutes(proposal, "first")
        second_minutes = self.minutes(proposal, "second")
        self.assertEqual(first_minutes, 120)
        self.assertEqual(second_minutes, 60)
        self.assertTrue(any("Second：P" in warning and "60 分钟" in warning
                            for warning in proposal["warnings"]))

    def test_existing_plan_is_never_overlapped(self):
        existing = [{"id": "busy", "taskId": "other", "title": "Class",
                     "startAt": "2026-10-01T19:30:00+08:00",
                     "endAt": "2026-10-01T20:30:00+08:00"}]
        body = request([task("one", "Assignment", "2026-10-02", "22:00")], existing=existing)
        proposal = build_proposal(body, {"one": 180})
        busy_start = datetime.fromisoformat(existing[0]["startAt"])
        busy_end = datetime.fromisoformat(existing[0]["endAt"])
        for block in proposal["blocks"]:
            start, end = datetime.fromisoformat(block["startAt"]), datetime.fromisoformat(block["endAt"])
            self.assertFalse(start < busy_end and busy_start < end)
        self.assertEqual(self.minutes(proposal, "one"), 180)

    def test_work_just_over_ninety_minutes_avoids_a_tiny_final_block(self):
        body = request([task("one", "Assignment", "2026-10-02", "22:00")])
        proposal = build_proposal(body, {"one": 100})
        durations = [(datetime.fromisoformat(block["endAt"]) -
                      datetime.fromisoformat(block["startAt"])).total_seconds() / 60
                     for block in proposal["blocks"]]
        self.assertEqual(durations, [50, 50])

        proposal = build_proposal(body, {"one": 120})
        durations = [(datetime.fromisoformat(block["endAt"]) -
                      datetime.fromisoformat(block["startAt"])).total_seconds() / 60
                     for block in proposal["blocks"]]
        self.assertEqual(durations, [60, 60])

    def test_impossible_task_reports_p0_and_missing_minutes(self):
        body = request([task("urgent", "Urgent", "2026-10-01", "20:00")])
        proposal = build_proposal(body, {"urgent": 180})
        self.assertEqual(self.minutes(proposal, "urgent"), 60)
        self.assertIn("Urgent：P0，DDL 前尚缺 120 分钟", proposal["warnings"])

    def test_task_without_deadline_is_estimated_but_not_scheduled(self):
        body = request([task("open", "Read when free")])
        proposal = build_proposal(body, {"open": 45})
        self.assertEqual(proposal["estimates"], {"open": 45})
        self.assertEqual(proposal["blocks"], [])
        self.assertIn("Read when free：没有 DDL，未自动排程", proposal["warnings"])

    @staticmethod
    def minutes(proposal, task_id):
        return sum((datetime.fromisoformat(block["endAt"]) - datetime.fromisoformat(block["startAt"])).total_seconds() / 60
                   for block in proposal["blocks"] if block["taskId"] == task_id)


if __name__ == "__main__":
    unittest.main()
