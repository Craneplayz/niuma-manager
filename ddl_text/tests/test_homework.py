"""Replay synthetic live drafts; this does not re-evaluate the live model."""

import json
import unittest
from copy import deepcopy
from pathlib import Path

from ddl_text.contract_v02 import validate_request, validate_result
from ddl_text.extraction import compile_drafts
from ddl_text.tests.classification_cases import matches
from ddl_text.tests.homework_cases import CASES


class HomeworkTests(unittest.TestCase):
    def test_homework_drafts_preserve_routing_in_both_consumers(self):
        from server.text_contract import validate_result as server_validate
        path = Path(__file__).resolve().parents[1] / "docs/archive/2026-10-06-issue111-homework-live.json"
        rows = json.loads(path.read_text(encoding="utf-8"))["cases"]
        cases = {c["name"]: c for c in CASES}
        self.assertEqual({r["name"] for r in rows}, set(cases))
        for row in rows:
            with self.subTest(name=row["name"]):
                case = cases[row["name"]]
                validate_request(case["request"])
                record = case["request"]["records"][0]
                tasks = compile_drafts(row["model_draft"], record)
                result = deepcopy(row["result"])
                result["tasks"] = tasks
                result["record_results"][0]["warnings"] = list(tasks.warnings)
                result = validate_result(case["request"], result)
                self.assertEqual(result, row["result"])
                self.assertEqual(server_validate(case["request"], result), result)
                self.assertTrue(matches(case, result))
                for task in result["tasks"]:
                    self.assertEqual(task["course_or_project"], record["course_or_project"])

    def test_unknown_file_deadline_is_not_equivalent_to_no_deadline(self):
        case = CASES[0]
        candidate = {"notice_type": "create", "needs_review": True,
                     "deadline": {"value": None, "status": "unknown"}}
        result = {"tasks": [candidate], "errors": [], "record_results": [{"warnings": []}]}
        self.assertTrue(matches(case, result))
        candidate["deadline"]["status"] = "not_provided"
        self.assertFalse(matches(case, result))

    def test_optional_assignment_must_not_be_auto_eligible(self):
        case = next(c for c in CASES if c["name"] == "homework-optional")
        candidate = {"notice_type": "create", "needs_review": False,
                     "deadline": {"value": None, "status": "not_provided"}}
        result = {"tasks": [candidate], "errors": [], "record_results": [{"warnings": []}]}
        self.assertFalse(matches(case, result))


if __name__ == "__main__":
    unittest.main()
