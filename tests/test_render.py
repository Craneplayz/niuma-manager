"""User-visible details must survive conversion from JSON to text."""

import unittest
from copy import deepcopy

from ddl_text.render import render_text
from tests.test_pipeline import example


class RenderTests(unittest.TestCase):
    def test_requirements_not_present_in_title_are_visible(self):
        result = example("absolute")["expected"]
        result["tasks"][0]["description"] = "报告须为 PDF，附源代码，上传教学网。"
        original = deepcopy(result)
        rendered = render_text(result, "offline")
        self.assertIn("具体要求：报告须为 PDF，附源代码，上传教学网。", rendered)
        self.assertEqual(result, original)

    def test_original_date_remains_visible_when_uncertain_or_conflicting(self):
        for case in ("relative-no-base", "conflict", "join-before-event"):
            with self.subTest(case=case):
                result = example(case)["expected"]
                task = result["tasks"][0]
                rendered = render_text(result, "offline")
                self.assertIn("时间原文：" + task["deadline"]["raw"], rendered)
                self.assertIn("待核实：", rendered)
                if task["deadline"]["value"] is None:
                    self.assertIn("截止日期：待核实", rendered)
                if case == "join-before-event":
                    self.assertIn("参考完成时间：", rendered)
                for alternative in task["deadline"]["alternatives"]:
                    self.assertIn(alternative, rendered)

    def test_missing_details_do_not_print_none_or_empty_labels(self):
        result = example("no-date")["expected"]
        result["tasks"][0]["description"] = " "
        rendered = render_text(result, "offline")
        self.assertNotIn("具体要求：", rendered)
        self.assertNotIn("时间原文：", rendered)
        self.assertNotIn("None", rendered)
        self.assertIn("截止日期：待核实", rendered)


if __name__ == "__main__":
    unittest.main()
