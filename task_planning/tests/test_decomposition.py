import json
import unittest
from unittest.mock import patch

from task_planning import decomposition


def request(**task_updates):
    task = {
        "id": "task-1",
        "title": "提交实验报告",
        "description": "周五提交",
        "deadline": "2026-10-02T17:00:00+08:00",
        "current_progress": None,
        "user_scope": None,
        "source_materials": [],
    }
    task.update(task_updates)
    return {
        "schema_version": "0.1-internal",
        "now": "2026-09-30T12:00:00+08:00",
        "timezone": "Asia/Shanghai",
        "task": task,
        "conversation": [],
        "preferences": {},
        "availability": {},
    }


def assessment(complexity):
    return {
        "summary": "完成当前剩余工作",
        "complexity": complexity,
        "confidence": 0.8,
        "known_facts": [{"text": "周五提交", "source": "task"}],
        "assumptions": [],
        "missing_information": [],
    }


def subtask(identifier="step-1", depends_on=None):
    return {
        "id": identifier,
        "title": "完成可检查的行动",
        "outcome": "留下可提交的结果",
        "duration_minutes": 30,
        "depends_on": depends_on or [],
        "scheduled_start": None,
        "scheduled_end": None,
        "source_basis": ["task"],
        "assumption": False,
    }


class RequestValidationTest(unittest.TestCase):
    def test_accepts_extracted_document_text_and_conversation(self):
        body = request(source_materials=[{"id": "brief", "name": "实验要求.pdf", "text": "包含方法、结果和讨论"}])
        body["conversation"] = [{"role": "user", "content": "实验已做完，数据也整理好了"}]
        self.assertIs(decomposition.validate_request(body), body)

    def test_rejects_duplicate_source_ids_and_unbounded_text(self):
        body = request(source_materials=[
            {"id": "brief", "name": "a", "text": "one"},
            {"id": "brief", "name": "b", "text": "two"},
        ])
        with self.assertRaisesRegex(ValueError, "unique"):
            decomposition.validate_request(body)
        body = request(source_materials=[{"id": "brief", "name": "a", "text": "x" * (decomposition.MAX_SOURCE_CHARS + 1)}])
        with self.assertRaisesRegex(ValueError, "too large"):
            decomposition.validate_request(body)


class DraftValidationTest(unittest.TestCase):
    def test_requires_questions_before_planning_when_context_is_insufficient(self):
        draft = {
            "status": "needs_clarification",
            "assessment": {**assessment("uncertain"), "missing_information": ["实验是否完成"]},
            "questions": [{
                "id": "progress",
                "prompt": "实验和数据处理分别完成到哪一步？",
                "why": "答案决定计划是否包含实验和数据分析",
                "answer_options": ["尚未实验", "已有数据", "已有初稿"],
            }],
            "plan": None,
        }
        self.assertIs(decomposition.validate_model_draft(draft), draft)
        draft["plan"] = {"goal": "premature"}
        with self.assertRaisesRegex(ValueError, "no plan"):
            decomposition.validate_model_draft(draft)

    def test_atomic_action_stays_as_one_subtask(self):
        draft = {
            "status": "no_split",
            "assessment": assessment("atomic"),
            "questions": [],
            "plan": {"goal": "打印作业", "strategy": "直接执行", "warnings": [], "subtasks": [subtask()]},
        }
        self.assertIs(decomposition.validate_model_draft(draft), draft)
        draft["plan"]["subtasks"].append(subtask("step-2"))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            decomposition.validate_model_draft(draft)

    def test_multi_step_plan_requires_dependencies_to_exist(self):
        draft = {
            "status": "ready_to_plan",
            "assessment": assessment("multi_step"),
            "questions": [],
            "plan": {
                "goal": "提交报告",
                "strategy": "只安排尚未完成的报告工作",
                "warnings": ["尚未提供精确可用时间，因此暂不排入具体时段"],
                "subtasks": [subtask(), subtask("step-2", ["step-1"])],
            },
        }
        self.assertIs(decomposition.validate_model_draft(draft), draft)
        draft["plan"]["subtasks"][1]["depends_on"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "invalid id"):
            decomposition.validate_model_draft(draft)
        draft["plan"]["subtasks"][0]["depends_on"] = ["step-2"]
        draft["plan"]["subtasks"][1]["depends_on"] = ["step-1"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            decomposition.validate_model_draft(draft)

    def test_rejects_half_scheduled_and_reversed_blocks(self):
        first = subtask()
        second = subtask("step-2")
        draft = {
            "status": "ready_to_plan",
            "assessment": assessment("multi_step"),
            "questions": [],
            "plan": {"goal": "提交报告", "strategy": "按依赖推进", "warnings": [], "subtasks": [first, second]},
        }
        first["scheduled_start"] = "2026-10-01T19:00:00+08:00"
        with self.assertRaisesRegex(ValueError, "both be set"):
            decomposition.validate_model_draft(draft)
        first["scheduled_end"] = "2026-10-01T18:00:00+08:00"
        with self.assertRaisesRegex(ValueError, "earlier"):
            decomposition.validate_model_draft(draft)


class ModelAdapterTest(unittest.TestCase):
    def test_skill_requires_contextual_decisions_and_model_output_is_validated(self):
        body = request()
        draft = {
            "status": "needs_clarification",
            "assessment": {**assessment("uncertain"), "missing_information": ["当前进度"]},
            "questions": [{"id": "progress", "prompt": "目前完成到哪一步？", "why": "决定剩余步骤", "answer_options": []}],
            "plan": None,
        }
        options = {"key": "test", "model": "test", "endpoint": "https://example.test/v1"}
        with patch.object(decomposition.ChatClient, "complete_json", return_value=json.dumps(draft, ensure_ascii=False)) as call:
            self.assertEqual(decomposition.generate_decomposition(body, options), draft)
        instructions, payload, settings = call.call_args.args
        self.assertIn("Do not classify by title and select a fixed task template", instructions)
        self.assertIn("one to three concise questions", instructions)
        self.assertEqual(json.loads(payload), body)
        self.assertEqual(settings.model, "test")

    def test_model_cannot_invent_sources_or_schedule_without_availability(self):
        body = request()
        draft = {
            "status": "ready_to_plan",
            "assessment": assessment("multi_step"),
            "questions": [],
            "plan": {
                "goal": "提交报告",
                "strategy": "按上下文推进",
                "warnings": [],
                "subtasks": [subtask(), subtask("step-2")],
            },
        }
        draft["plan"]["subtasks"][0]["source_basis"] = ["source:invented"]
        with self.assertRaisesRegex(ValueError, "not present"):
            decomposition.validate_model_draft(draft, body)
        draft["plan"]["subtasks"][0]["source_basis"] = ["task"]
        draft["plan"]["subtasks"][0]["scheduled_start"] = "2026-10-01T19:00:00+08:00"
        draft["plan"]["subtasks"][0]["scheduled_end"] = "2026-10-01T19:30:00+08:00"
        with self.assertRaisesRegex(ValueError, "availability"):
            decomposition.validate_model_draft(draft, body)

    def test_schedule_rejects_past_deadline_overlap_and_unfinished_dependencies(self):
        body = request()
        body["availability"] = {
            "windows": [
                {"start": "2026-10-01T18:00:00+08:00", "end": "2026-10-01T22:00:00+08:00"},
                {"start": "2026-10-02T15:00:00+08:00", "end": "2026-10-02T18:00:00+08:00"},
            ],
            "existing_blocks": [],
        }
        first, second = subtask(), subtask("step-2", ["step-1"])
        draft = {
            "status": "ready_to_plan",
            "assessment": assessment("multi_step"),
            "questions": [],
            "plan": {"goal": "提交报告", "strategy": "按依赖推进", "warnings": [], "subtasks": [first, second]},
        }
        first["scheduled_start"], first["scheduled_end"] = "2026-10-01T19:00:00+08:00", "2026-10-01T20:00:00+08:00"
        second["scheduled_start"], second["scheduled_end"] = "2026-10-01T19:30:00+08:00", "2026-10-01T20:30:00+08:00"
        with self.assertRaisesRegex(ValueError, "overlap"):
            decomposition.validate_model_draft(draft, body)
        second["scheduled_start"], second["scheduled_end"] = "2026-10-02T16:30:00+08:00", "2026-10-02T17:30:00+08:00"
        with self.assertRaisesRegex(ValueError, "after the deadline"):
            decomposition.validate_model_draft(draft, body)
        first["scheduled_start"], first["scheduled_end"] = "2026-09-30T10:00:00+08:00", "2026-09-30T11:00:00+08:00"
        second["scheduled_start"], second["scheduled_end"] = "2026-10-01T20:00:00+08:00", "2026-10-01T21:00:00+08:00"
        with self.assertRaisesRegex(ValueError, "past"):
            decomposition.validate_model_draft(draft, body)


if __name__ == "__main__":
    unittest.main()
