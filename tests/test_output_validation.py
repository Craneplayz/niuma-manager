"""Reject inconsistent candidate meanings, not just malformed field types."""

import unittest
from copy import deepcopy

from ddl_text.contracts import ContractError, normalize_record, validate_tasks
from ddl_text.pipeline import process_request
from ddl_text.render import render_text
from tests.fake_adapters import ControlledAdapter
from tests.test_pipeline import example


class OutputValidationTests(unittest.TestCase):
    def test_invalid_deadline_combinations_are_rejected(self):
        case = example()
        record = normalize_record(case["input"]["records"][0])
        for change in ({"kind": "unknown"}, {"timezone": None},
                       {"status": "explicit", "kind": "reference"}):
            tasks = deepcopy(case["expected"]["tasks"])
            tasks[0]["deadline"].update(change)
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_tasks(tasks, record)

    def test_conflict_needs_two_distinct_candidates(self):
        case = example("conflict")
        record = normalize_record(case["input"]["records"][0])
        for alternatives in (["2026-09-25"], ["2026-09-25", "2026-09-25"]):
            tasks = deepcopy(case["expected"]["tasks"])
            tasks[0]["deadline"]["alternatives"] = alternatives
            with self.subTest(alternatives=alternatives), self.assertRaises(ContractError):
                validate_tasks(tasks, record)

    def test_explicit_date_does_not_hide_other_uncertainty(self):
        case = example()
        record = normalize_record(case["input"]["records"][0])
        for change in ({"course_basis": "inferred", "course_or_project": "推断课程"},
                       {"notice_type": "uncertain"},
                       {"notice_type": "cancel", "target_reference": None}):
            tasks = deepcopy(case["expected"]["tasks"])
            tasks[0].update(change, needs_review=False, review_reasons=[])
            with self.subTest(change=change):
                with self.assertRaises(ContractError):
                    validate_tasks(tasks, record)
                tasks[0].update(needs_review=True, review_reasons=["归属或对象待核实。"])
                validate_tasks(tasks, record)

    def test_provided_course_and_create_target_cannot_be_fabricated(self):
        case = example()
        record = normalize_record(case["input"]["records"][0])
        for change in ({"course_basis": "provided", "course_or_project": "伪造课程"},
                       {"notice_type": "create", "target_reference": "旧任务"}):
            tasks = deepcopy(case["expected"]["tasks"])
            tasks[0].update(change)
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_tasks(tasks, record)

    def test_invalid_output_surfaces_failure_not_empty_success(self):
        case = example()
        tasks = case["expected"]["tasks"]
        tasks[0]["deadline"]["kind"] = "unknown"
        result = process_request(case["input"], ControlledAdapter("output", tasks))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"][0]["code"], "invalid_agent_output")
        text = render_text(result, "offline")
        self.assertIn("处理失败", text)
        self.assertNotIn("未识别到待办", text)

    def test_text_preserves_identifiers_and_invalid_request_can_render(self):
        result = example()["expected"]
        text = render_text(result, "offline")
        self.assertIn("请求标识：" + result["request_id"], text)
        self.assertIn("候选标识：" + result["tasks"][0]["candidate_id"], text)
        self.assertIn("请求标识：未提供", render_text(process_request(None), "offline"))
