import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path

from ddl_text.adapters import ReplayAdapter
from ddl_text.config import Settings
from ddl_text.pipeline import process_request
from tests.fake_adapters import ControlledAdapter, NetworkForbiddenReplay, TimeoutFirstAdapter


ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "docs/examples/text-contract-v0.1.json").read_text(encoding="utf-8"))["cases"]


def example(name="absolute"):
    return deepcopy(next(case for case in CASES if case["case_id"] == name))


class PipelineTests(unittest.TestCase):
    def test_contract_cases_without_network(self):
        # Messages are intentionally stable categories, not fixture prose.
        for case in CASES:
            with self.subTest(case=case["case_id"]):
                result = process_request(case["input"], NetworkForbiddenReplay(CASES))
                expected = deepcopy(case["expected"])
                for error in result["errors"]:
                    error.pop("message")
                for error in expected["errors"]:
                    error.pop("message")
                self.assertEqual(result, expected)

    def test_adapter_can_be_replaced_without_pipeline_change(self):
        request = example()["input"]
        replay = process_request(request, ReplayAdapter(CASES))
        empty = process_request(request, ControlledAdapter("empty"))
        self.assertEqual(replay["status"], "success")
        self.assertEqual(empty["status"], "no_tasks")

    def test_minimal_input_gets_explicit_unknown_metadata(self):
        request = {"contract_version": "0.1", "request_id": "r", "records": [{"input_id": "a", "text": "synthetic"}]}
        result = process_request(request, ControlledAdapter("empty"))
        self.assertEqual(result["status"], "no_tasks")

    def test_invalid_record_does_not_discard_valid_record(self):
        request = example()["input"]
        request["records"].append({"input_id": "broken", "text": " "})
        result = process_request(request, ReplayAdapter(CASES))
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["errors"][0]["code"], "invalid_input")

    def test_empty_success_plus_failure_is_partial(self):
        request = example("no-task")["input"]
        request["records"].append({"input_id": "broken", "text": ""})
        result = process_request(request, ReplayAdapter(CASES))
        self.assertEqual((result["status"], result["tasks"]), ("partial_success", []))

    def test_invalid_envelope_never_calls_adapter(self):
        for request in [None, {}, {"contract_version": "8"},
                        {"contract_version": "0.1", "request_id": "r", "records": [{"text": "a"}]},
                        {"contract_version": "0.1", "request_id": "r", "records": [{"input_id": "x"}] * 2}]:
            with self.subTest(request=request):
                result = process_request(request, ControlledAdapter("timeout"))
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["record_results"], [])

    def test_invalid_metadata_is_record_error(self):
        for key, value in [("platform", []), ("published_at", None), ("timezone", 4), ("context", [None])]:
            request = example()["input"]
            request["records"][0][key] = value
            self.assertEqual(process_request(request)["errors"][0]["code"], "invalid_input")

    def test_blank_input_is_not_no_tasks(self):
        result = process_request(example("empty")["input"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"][0]["code"], "invalid_input")

    def test_changed_input_cannot_reuse_fixture_result(self):
        request = example()["input"]
        request["records"][0]["text"] = "PRIVATE-TEXT-SENTINEL"
        result = process_request(request, ReplayAdapter(CASES))
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("PRIVATE-TEXT-SENTINEL", json.dumps(result))

    def test_missing_live_adapter_does_not_fallback(self):
        for settings in [Settings(mode="real"), Settings(mode="real", model="configured-id", endpoint="https://example.invalid")]:
            result = process_request(example()["input"], settings=settings)
            self.assertEqual(result["errors"][0]["code"], "agent_unavailable")
            self.assertFalse(result["errors"][0]["retryable"])

    def test_retry_is_bounded_and_can_recover(self):
        request = example()["input"]
        self.assertEqual(process_request(request, ControlledAdapter("retry"))["status"], "failed")
        result = process_request(request, ControlledAdapter("retry"), Settings(max_retries=1))
        self.assertEqual(result["status"], "no_tasks")
        with self.assertLogs("ddl_text", level="INFO") as logs:
            result = process_request(request, ControlledAdapter("malformed"), Settings(max_retries=2))
        self.assertEqual(result["status"], "failed")
        self.assertIn("attempts=3", logs.output[0])

    def test_nonretryable_failure_does_not_retry(self):
        result = process_request(example()["input"], ControlledAdapter("nonretryable"), Settings(max_retries=2))
        self.assertEqual(result["status"], "failed")

    def test_real_timeout_releases_next_record_and_worker(self):
        request = example()["input"]
        request["records"][0]["input_id"] = "slow"
        request["records"].append(deepcopy(example("no-task")["input"]["records"][0]))
        before = {p.pid for p in multiprocessing.active_children()}
        start = time.monotonic()
        result = process_request(request, TimeoutFirstAdapter(), Settings(timeout_seconds=1.5))
        self.assertLess(time.monotonic() - start, 8)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["errors"][0]["code"], "agent_timeout")
        self.assertEqual(result["record_results"][1]["status"], "no_tasks")
        self.assertEqual(before, {p.pid for p in multiprocessing.active_children()})

    def test_exception_and_audit_do_not_expose_body(self):
        with self.assertLogs("ddl_text", level="INFO") as logs:
            result = process_request(example()["input"], ControlledAdapter("secret"))
        combined = json.dumps(result) + str(logs.output)
        self.assertNotIn("PRIVATE-TEXT-SENTINEL", combined)
        self.assertNotIn("SECRET-KEY-SENTINEL", combined)

    def test_input_not_mutated_or_shared_across_requests(self):
        request = example()["input"]
        original = deepcopy(request)
        adapter = ControlledAdapter("context")
        for unused in range(2):
            self.assertEqual(process_request(request, adapter)["status"], "no_tasks")
            self.assertEqual(request, original)

    def test_malformed_or_fabricated_candidate_fails_entire_record(self):
        original = example()["expected"]["tasks"]
        variations = [{"oops": "wrong"}, deepcopy(original[0]), deepcopy(original[0]), deepcopy(original[0])]
        variations[1]["sources"][0]["url"] = "https://invented.invalid"
        variations[2]["evidence"][0]["quote"] = "not in input"
        variations[3]["deadline"]["status"] = "unknown"
        for broken in variations[1:]:
            broken["candidate_id"] = "second-candidate"
        for broken in variations:
            result = process_request(example()["input"], ControlledAdapter("static", [original[0], broken]))
            self.assertEqual(result["tasks"], [])
            self.assertEqual(result["errors"][0]["code"], "invalid_agent_output")

    def test_configuration_rejects_unbounded_values(self):
        for kwargs in [{"timeout_seconds": float("nan")}, {"timeout_seconds": 0}, {"timeout_seconds": float("inf")},
                       {"max_retries": -1}, {"max_retries": 3}, {"max_retries": True}]:
            with self.assertRaises(ValueError):
                Settings(**kwargs)

    def test_unknown_output_fields_not_forwarded(self):
        tasks = example()["expected"]["tasks"]
        tasks[0]["debug_private_context"] = "PRIVATE-TEXT-SENTINEL"
        result = process_request(example()["input"], ControlledAdapter("static", tasks))
        self.assertEqual(result["errors"][0]["code"], "invalid_agent_output")
        self.assertNotIn("PRIVATE-TEXT-SENTINEL", json.dumps(result))


class CliTests(unittest.TestCase):
    def run_cli(self, *args, input=None):
        # Tests must never start a paid call after a developer fills their .env.
        environment = dict(os.environ)
        environment["DEEPSEEK_API_KEY"] = ""
        return subprocess.run([sys.executable, "-m", "ddl_text", *args], cwd=ROOT, input=input,
                              encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
                              env=environment)

    def test_json_stdout_and_mode_stderr(self):
        result = self.run_cli("--demo", "multiple", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["tasks"]), 2)
        self.assertIn("mode=offline", result.stderr)

    def test_file_input_config_and_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "request.json"
            path.write_text(json.dumps(example()["input"]), encoding="utf-8-sig")
            result = self.run_cli("--input", str(path), "--replay", "docs/examples/text-contract-v0.1.json",
                                  "--config", "docs/examples/text-runner-config.json", "--format", "json", "--audit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "success")
        self.assertIn("attempts=1", result.stderr)
        self.assertNotIn(example()["input"]["records"][0]["text"], result.stderr)

    def test_stdin_and_live_unavailable(self):
        result = self.run_cli("--input", "-", "--mode", "real", "--format", "json", input=json.dumps(example()["input"]))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")
        self.assertIn("mode=real", result.stderr)

    def test_invalid_json_is_sanitized(self):
        result = self.run_cli("--input", "-", input="PRIVATE-TEXT-SENTINEL {")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("PRIVATE-TEXT-SENTINEL", result.stderr)

    def test_text_mode_identifies_replay(self):
        result = self.run_cli("--demo", "no-date")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("非模型识别", result.stdout)
        self.assertIn("待核实", result.stdout)

    def test_real_demo_rejected(self):
        result = self.run_cli("--demo", "absolute", "--mode", "real")
        self.assertEqual(result.returncode, 2)
        self.assertIn("只允许", result.stderr)


if __name__ == "__main__":
    unittest.main()
