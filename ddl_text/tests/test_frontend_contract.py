"""v0.2 behavior and HTTP boundary checks; never call the paid provider."""

import json
import threading
import unittest
from copy import deepcopy
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from ddl_text.adapters import AgentError, UnavailableAdapter
from ddl_text.config import Settings
from ddl_text.contracts import normalize_record
from ddl_text.contract_v02 import validate_request, validate_result
from ddl_text.extraction import ExtractionAdapter, OfflineDraftClient, compile_drafts
from ddl_text.http_server import RecognitionServer
from ddl_text.pipeline import process_request


def record(text="请提交报告。", **kwargs):
    return normalize_record(dict(input_id="notice", text=text, **kwargs))


def draft(r, **kwargs):
    return dict(dict(title="提交报告", description="按要求提交报告。", prerequisites=[],
                     notice_type="create", target=None, course=None,
                     evidence=[dict(input_id=r["input_id"], quote=r["text"])],
                     times=[], review_reasons=[]), **kwargs)


def request(r):
    return dict(contract_version="0.2", request_id="test:v02", records=[r])


def compile_task(r, d):
    return compile_drafts(json.dumps(dict(tasks=[d])), normalize_record(r, "0.2"))[0]


class FrontendContractTests(unittest.TestCase):
    def test_complete_no_deadline_is_not_provided(self):
        r = record()
        t = compile_task(r, draft(r))
        self.assertEqual(t["deadline"]["status"], "not_provided")
        self.assertFalse(t["needs_review"])
        self.assertIsNone(t["deadline"]["raw"])
        self.assertIn("title", t["evidence"][0]["supports"])

    def test_incomplete_or_ambiguous_deadline_stays_reviewable(self):
        for quality in ("partial", "summary"):
            r = record(text_quality=quality)
            t = compile_task(r, draft(r))
            self.assertEqual(t["deadline"]["status"], "unknown")
            self.assertTrue(t["needs_review"])
        r = record("请尽快交报告。")
        t = compile_task(r, draft(r, times=[dict(input_id="notice", quote="尽快", kind="deadline")]))
        self.assertEqual(t["deadline"]["status"], "unknown")
        self.assertEqual(t["deadline"]["raw"], "尽快")

    def test_conflict_keeps_verbatim_raw_and_all_evidence(self):
        r = record("截止日期有两个说法：2026-09-25或者2026-09-28，请交报告。")
        t = compile_task(r, draft(r, times=[dict(input_id="notice", quote=x, kind="deadline")
                                          for x in ("2026-09-25", "2026-09-28")]))
        self.assertEqual(t["deadline"]["status"], "conflict")
        self.assertIn(t["deadline"]["raw"], r["text"])
        self.assertEqual(t["deadline"]["alternatives"], ["2026-09-25", "2026-09-28"])
        self.assertEqual([e["quote"] for e in t["evidence"] if "deadline" in e["supports"]],
                         ["2026-09-25", "2026-09-28"])

    def test_reference_known_and_unresolved(self):
        r = record("2026-09-25开会，请提前加群。")
        t = compile_task(r, draft(r, times=[dict(input_id="notice", quote="2026-09-25", kind="reference")]))
        self.assertEqual((t["deadline"]["kind"], t["deadline"]["status"]), ("reference", "inferred"))
        r = record("明天开会，请提前加群。")
        t = compile_task(r, draft(r, times=[dict(input_id="notice", quote="明天", kind="reference")]))
        self.assertEqual((t["deadline"]["kind"], t["deadline"]["status"]), ("unknown", "unknown"))
        self.assertIn("参考", t["deadline"]["basis"])

    def test_evidence_roles_are_derived_from_original_draft_fields(self):
        r = record("计算导论课程报告延期至2026-09-30，先查收邮件。")
        t = compile_task(r, draft(r, notice_type="postpone", prerequisites=["查收邮件"],
            target=dict(input_id="notice", quote="课程报告"),
            course=dict(input_id="notice", quote="计算导论", value="计算导论"),
            times=[dict(input_id="notice", quote="2026-09-30", kind="deadline")]))
        evidence = {e["quote"]: e["supports"] for e in t["evidence"]}
        self.assertEqual(evidence["课程报告"], ["target_reference"])
        self.assertEqual(evidence["计算导论"], ["course_or_project"])
        self.assertEqual(evidence["2026-09-30"], ["deadline"])
        self.assertIn("notice_type", evidence[r["text"]])
        self.assertIn("prerequisites", evidence[r["text"]])

    def test_fabricated_evidence_is_rejected(self):
        r = record()
        with self.assertRaises(AgentError):
            compile_task(r, draft(r, evidence=[dict(input_id="notice", quote="捏造的内容")]))

    def test_empty_and_missing_records_are_visible_failures(self):
        r = record()
        empty = deepcopy(r); empty.update(input_id="empty", text="", text_quality="missing")
        req = request(r); req["records"].append(empty)
        original = deepcopy(req)
        adapter = ExtractionAdapter(OfflineDraftClient([dict(record=r, response=dict(tasks=[draft(r)]))]))
        result = process_request(req, adapter)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["errors"][0]["code"], "invalid_input")
        self.assertEqual(validate_result(req, result), result)
        self.assertEqual(req, original)

    def test_global_context_identity_and_strict_required_fields(self):
        r = record()
        req = request(r)
        req["records"].append(dict(r, input_id="other"))
        context = dict(input_id="other", text="上下文", published_at=r["published_at"],
                       timezone=None, platform="unknown", source_url=None, source_label=None)
        r["context"] = [context]
        self.assertEqual(process_request(req)["errors"][0]["code"], "invalid_request")
        r["context"] = []
        del r["source_url"]
        self.assertEqual(process_request(req)["record_results"], [])

    def test_no_silent_upgrade_of_v01_or_old_candidates(self):
        r = record()
        t = compile_drafts(json.dumps(dict(tasks=[draft(r)])), r)[0]
        self.assertEqual(t["deadline"]["status"], "unknown")
        self.assertNotIn("supports", t["evidence"][0])
        req = request(r)
        with patch("ddl_text.pipeline.run_attempt", return_value=[t]):
            result = process_request(req)
        self.assertEqual(result["errors"][0]["code"], "invalid_agent_output")

    def test_expired_batch_does_not_start_model(self):
        with patch("ddl_text.pipeline.run_attempt") as attempt:
            result = process_request(request(record()), budget_seconds=0)
        attempt.assert_not_called()
        self.assertEqual(result["errors"][0]["code"], "agent_timeout")

    def test_empty_batch(self):
        req = dict(contract_version="0.2", request_id="empty", records=[])
        self.assertEqual(validate_result(req, process_request(req))["status"], "no_tasks")


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.server = RecognitionServer(("127.0.0.1", 0), UnavailableAdapter(), Settings(), token="test-only")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/recognize" % self.server.server_port

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def call(self, payload, token="test-only", content_type="application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        req = Request(self.url, raw, {"Authorization": "Bearer " + token, "Content-Type": content_type})
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_bare_json_failure_and_exact_frontend_shape(self):
        req = request(record())
        status, result = self.call(req)
        self.assertEqual(status, 200)
        self.assertEqual(validate_result(req, result), result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"][0]["code"], "agent_unavailable")

    def test_auth_json_version_and_size_boundaries(self):
        self.assertEqual(self.call({}, token="wrong")[0], 401)
        self.assertEqual(self.call({}, content_type="text/plain")[0], 415)
        self.assertEqual(self.call(dict(contract_version="0.1"))[0], 400)
        self.assertEqual(self.call(b'{"x":1,"x":2}')[0], 400)
        self.assertEqual(self.call(b'{"x":NaN}')[0], 400)
        self.assertEqual(self.call(b'{private-invalid-json')[0], 400)
        self.assertEqual(self.call(b' ' * 2_000_001)[0], 413)

    def test_busy_rejects_without_another_extraction(self):
        self.server.active.acquire()
        try:
            self.assertEqual(self.call(request(record()))[0], 503)
        finally:
            self.server.active.release()

    def test_http_uses_shared_budget_and_budget_is_bounded(self):
        with patch('ddl_text.http_server.process_request', return_value={}) as process:
            self.assertEqual(self.call(request(record()))[0], 200)
            self.assertEqual(process.call_args.kwargs['budget_seconds'], 90)
        for budget in (0, 91, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                RecognitionServer(('127.0.0.1', 0), UnavailableAdapter(), Settings(), budget_seconds=budget)


if __name__ == "__main__":
    unittest.main()
