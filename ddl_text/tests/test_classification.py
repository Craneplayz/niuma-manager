"""Classification policy, evidence, process transport and contract regressions."""

import json
import io
import sqlite3
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from ddl_text.adapters import AgentError
from ddl_text.contract_v02 import validate_result
from ddl_text.contracts import normalize_record
from ddl_text.extraction import ExtractionAdapter, OfflineDraftClient, compile_drafts
from ddl_text.pipeline import process_request
from ddl_text.tests.classification_cases import CASES, case, matches
from ddl_text.tests.teaching_section_cases import CASES as SECTION_CASES
from ddl_text.tests.test_extraction import draft, ref, time_ref


def record(text="请于2026-10-01提交报告。", **metadata):
    return case("test", text, [], **metadata)["request"]["records"][0]


def proposal(r, value="2026-10-01", state="current", reasons=(), **kwargs):
    return draft(r, times=[time_ref(r, r["text"], value)] if value else [],
                 review_reasons=list(reasons), relevance={"state": state, "reason": "通知中的历史有效性说明。",
                 "evidence": [] if state == "current" else [ref(r, r["text"])]}, **kwargs)


def compile(r, *items):
    return compile_drafts(json.dumps({"tasks": items}, ensure_ascii=False), r)


class ClassificationTests(unittest.TestCase):
    def test_recorded_teaching_sections_preserve_actions_course_and_classification(self):
        from server.text_contract import validate_result as server_validate
        root = Path(__file__).resolve().parents[2]
        report = json.loads((root / "ddl_text/docs/archive/2026-09-30-issue77-teaching-sections.json").read_text(encoding="utf-8"))
        cases = {c['name']: c for c in SECTION_CASES}
        self.assertEqual({r['name'] for r in report['cases']}, set(cases))
        for row in report['cases']:
            with self.subTest(name=row['name']):
                c = cases[row['name']]
                self.assertEqual(row['request'], c['request'])
                record = c['request']['records'][0]
                tasks = compile_drafts(row['model_draft'], record)
                result = deepcopy(row['result'])
                result['tasks'] = tasks
                result['record_results'][0]['warnings'] = tasks.warnings
                self.assertEqual(result, row['result'])
                self.assertEqual(validate_result(c['request'], result), result)
                self.assertEqual(server_validate(c['request'], result), result)
                self.assertTrue(matches(c, result))
                for candidate in tasks:
                    self.assertTrue(candidate['title'].strip())
                    self.assertEqual(candidate['course_or_project'], record['course_or_project'])

    def test_recorded_live_classifications_replay_without_model_calls(self):
        root = Path(__file__).resolve().parents[2]
        report = json.loads((root / "ddl_text/docs/archive/2026-09-30-issue68-classification-final.json").read_text(encoding="utf-8"))
        cases = {c["name"]: c for c in CASES}
        self.assertEqual({r["name"] for r in report["cases"]}, set(cases))
        for row in report["cases"]:
            with self.subTest(name=row["name"]):
                c = cases[row["name"]]
                tasks = compile_drafts(row["model_draft"], c["request"]["records"][0])
                result = deepcopy(row["result"])
                result["tasks"] = tasks
                result["record_results"][0]["warnings"] = tasks.warnings
                self.assertEqual(result, row["result"])
                self.assertEqual(validate_result(c["request"], result), result)
                self.assertTrue(matches(c, result))

    def test_make_up_action_may_update_or_create_but_never_reuse_obsolete_due(self):
        c = next(c for c in CASES if c["name"] == "ongoing")
        r = c["request"]["records"][0]
        for notice in ("create", "update"):
            p = proposal(r, None, "ongoing", notice_type=notice,
                         target=ref(r, "课程报告") if notice == "update" else None)
            p["times"] = [time_ref(r, "新的补交期限尚未确定", derivation="unresolved")]
            tasks = compile(r, p)
            result = {"tasks": tasks, "errors": [], "record_results": [{"warnings": []}]}
            self.assertTrue(matches(c, result))
            tasks[0]["deadline"].update(value="2024-10-01", status="explicit", precision="date")
            self.assertFalse(matches(c, result))

    def test_backend_persists_only_kept_candidates_and_retains_audit(self):
        from server import text_contract
        with patch.dict(sys.modules, {"text_contract": text_contract}):
            from server import recognition
        root = Path(__file__).resolve().parents[2]
        fixture = json.loads((root / "contracts/text-to-app/text-classification-v02.json").read_text(encoding="utf-8"))
        handler = Mock(command="POST")
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            recognition.initialize(db)
            body = io.BytesIO(json.dumps(fixture["expected"]).encode())
            with patch.dict("os.environ", {"TEXT_HARNESS_URL": "http://synthetic.invalid/recognize"}), \
                    patch.object(recognition, "urlopen", return_value=body):
                recognition.route(handler, db, "synthetic-user", "/api/recognition/batches", fixture["request"], ValueError)
            handler.reply.assert_called_once()
            status, batches = handler.reply.call_args.args
            self.assertEqual(status, 200)
            self.assertEqual(batches[0]["result"], fixture["expected"])
            self.assertEqual([i["candidate"] for i in batches[0]["items"]], fixture["expected"]["tasks"])
            filtered = {r["input_id"] for r in fixture["expected"]["record_results"] if r["status"] == "no_tasks"}
            self.assertFalse(filtered.intersection(i["candidate"]["input_id"] for i in batches[0]["items"]))
        finally:
            db.close()

    def test_shared_examples_compile_and_validate_in_both_consumers(self):
        from server.text_contract import validate_result as server_validate
        root = Path(__file__).resolve().parents[2]
        fixture = json.loads((root / "contracts/text-to-app/text-classification-v02.json").read_text(encoding="utf-8"))
        entries = json.loads((root / "ddl_text/tests/fixtures/text-classification-drafts.json").read_text(encoding="utf-8"))
        result = process_request(fixture["request"], ExtractionAdapter(OfflineDraftClient(entries)))
        self.assertEqual(result, fixture["expected"])
        self.assertEqual(server_validate(fixture["request"], result), result)
        for c in CASES:
            with self.subTest(name=c["name"]):
                rid = c["request"]["records"][0]["input_id"]
                single = dict(result, tasks=[t for t in result["tasks"] if t["input_id"] == rid],
                              record_results=[r for r in result["record_results"] if r["input_id"] == rid])
                self.assertTrue(matches(c, single))

    def test_current_dated_and_undated_actions(self):
        r = record()
        for value in ("2026-10-01", None):
            tasks = compile(r, proposal(r, value))
            self.assertEqual(len(tasks), 1)
            self.assertFalse(tasks[0]["needs_review"])
            self.assertEqual(tasks[0]["deadline"]["status"], "explicit" if value else "not_provided")

    def test_information_is_audited_without_candidate(self):
        tasks = compile(record("活动圆满举行。"))
        self.assertEqual(tasks, [])
        self.assertTrue(tasks.warnings[0].startswith("no_action:"))

    def test_old_deadline_filtered_and_evidence_retained(self):
        r = record("请于2024-10-01提交报告。")
        tasks = compile(r, proposal(r, "2024-10-01"))
        self.assertEqual(tasks, [])
        self.assertIn(r["input_id"], tasks.warnings[0])
        self.assertIn(r["text"], tasks.warnings[0])

    def test_thirty_day_boundary_and_recent_overdue(self):
        r = record()
        for value, filtered in (("2026-08-30", True), ("2026-08-31", False), ("2026-09-29", False)):
            with self.subTest(value=value):
                tasks = compile(r, proposal(r, value))
                self.assertEqual(not tasks, filtered)
                if tasks:
                    self.assertTrue(tasks[0]["needs_review"])

    def test_date_only_today_is_not_expired(self):
        r = record(collected_at="2026-09-30T23:59:59+08:00")
        self.assertFalse(compile(r, proposal(r, "2026-09-30"))[0]["needs_review"])

    def test_comparison_uses_source_timezone_and_actual_instant(self):
        r = record(collected_at="2026-09-30T01:00:00Z")  # 09:00 Shanghai
        self.assertFalse(compile(r, proposal(r, "2026-09-30T09:00:00+08:00"))[0]["needs_review"])
        self.assertTrue(compile(r, proposal(r, "2026-09-30T08:59:59+08:00"))[0]["needs_review"])

    def test_missing_collection_snapshot_does_not_use_clock(self):
        r = record(collected_at=None)
        self.assertEqual(len(compile(r, proposal(r, "2024-10-01"))), 1)

    def test_old_publication_and_future_deadline_is_current(self):
        r = record(published_at={"value": "2020-09-01", "precision": "date", "basis": "source"})
        self.assertFalse(compile(r, proposal(r))[0]["needs_review"])

    def test_undated_old_publication_alone_does_not_delete_action(self):
        r = record("请加入课程群。", published_at={"value": "2020-09-01", "precision": "date", "basis": "source"})
        self.assertTrue(compile(r, proposal(r, None))[0]["needs_review"])

    def test_explicit_history_without_date_is_filtered(self):
        r = record("旧学期已经结束，归档要求：请加入课程群。")
        self.assertEqual(compile(r, proposal(r, None, "historical")), [])

    def test_future_time_conflicting_with_history_is_reviewable(self):
        r = record()
        self.assertTrue(compile(r, proposal(r, state="historical"))[0]["needs_review"])

    def test_ongoing_obligation_survives_expired_deadline(self):
        r = record("原截止为2024-10-01，未交者仍须补交。")
        self.assertTrue(compile(r, proposal(r, "2024-10-01", "ongoing"))[0]["needs_review"])

    def test_changes_always_require_review_and_survive_history(self):
        r = record("取消旧报告，原截止为2024-10-01。")
        for notice in ("update", "postpone", "cancel"):
            tasks = compile(r, proposal(r, "2024-10-01", "historical", notice_type=notice, target=ref(r, "报告")))
            self.assertEqual(tasks[0]["notice_type"], notice)
            self.assertTrue(tasks[0]["needs_review"])

    def test_semantic_uncertainty_and_partial_text_prevent_age_filter(self):
        for metadata, state, reasons in (({}, "uncertain", ()), ({"text_quality": "partial"}, "historical", ()),
                                        ({}, "current", ("适用对象不明",))):
            r = record(**metadata)
            self.assertTrue(compile(r, proposal(r, "2024-10-01", state, reasons))[0]["needs_review"])

    def test_historical_filter_is_per_action(self):
        r = record("旧学期已结束，旧要求为加群。新要求为2026-10-01提交报告。")
        tasks = compile(r, proposal(r, None, "historical"), proposal(r))
        self.assertEqual(len(tasks), 1)
        self.assertTrue(tasks[0]["candidate_id"].endswith(":2"))
        self.assertTrue(tasks.warnings[0].startswith("history_filtered:"))

    def test_unknown_original_age_is_not_confused_with_normal_year_completion(self):
        for trusted in (True, False):
            r = record("10月1日交报告。")
            if not trusted:
                r["published_at"] = {"value": None, "precision": "unknown", "basis": "unknown"}
            p = proposal(r)
            p["times"] = [time_ref(r, r["text"], "2026-10-01", derivation="missing_year",
                                   anchor="published_at" if trusted else "collected_at")]
            tasks = compile(r, p)
            self.assertEqual(tasks[0]["deadline"]["status"], "explicit")
            self.assertEqual(tasks[0]["needs_review"], not trusted)

    def test_trusted_historical_year_completion_is_filtered(self):
        r = record("10月1日交报告。", published_at={"value": "2024-09-30", "precision": "date", "basis": "source"})
        p = proposal(r)
        p["times"] = [time_ref(r, r["text"], "2024-10-01", derivation="missing_year", anchor="published_at")]
        self.assertEqual(compile(r, p), [])

    def test_archived_notice_does_not_become_current_by_collection_year(self):
        r = record("旧学期已结束，归档要求：10月1日交报告。",
                   published_at={"value": None, "precision": "unknown", "basis": "unknown"})
        p = proposal(r, state="historical")
        p["times"] = [time_ref(r, "10月1日", "2026-10-01", derivation="missing_year", anchor="collected_at")]
        self.assertEqual(compile(r, p), [])

    def test_bad_relevance_evidence_fails_even_discarded_task(self):
        r = record()
        p = proposal(r, None, "historical")
        for refs in ([], [{"input_id": r["input_id"], "quote": "不存在的证据"}], [{"input_id": "other", "quote": r["text"]}]):
            p["relevance"]["evidence"] = refs
            with self.assertRaises(AgentError):
                compile(r, p)

    def test_malformed_kept_or_discarded_draft_fails_whole_record(self):
        r = record()
        p = proposal(r, None, "historical")
        p["evidence"][0]["quote"] = "invented"
        with self.assertRaises(AgentError):
            compile(r, proposal(r), p)

    def test_manual_and_legacy_paths_keep_existing_behavior(self):
        for r in (record(platform="unknown"), normalize_record(record())):
            tasks = compile(r, proposal(r, "2024-10-01"))
            self.assertEqual(len(tasks), 1)
            self.assertFalse(tasks[0]["needs_review"])

    def test_process_transports_audit_without_changing_public_schema(self):
        r = record("请于2024-10-01提交报告。")
        request = {"contract_version": "0.2", "request_id": "transport-test", "records": [r]}
        original = deepcopy(request)
        client = OfflineDraftClient([{"record": r, "response": {"tasks": [proposal(r, "2024-10-01")]}}])
        result = process_request(request, ExtractionAdapter(client))
        self.assertEqual(validate_result(request, result), result)
        self.assertEqual(result["status"], "no_tasks")
        self.assertTrue(result["record_results"][0]["warnings"][0].startswith("history_filtered:"))
        self.assertEqual(request, original)


if __name__ == "__main__":
    unittest.main()
