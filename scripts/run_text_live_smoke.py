"""Explicitly paid, synthetic-only stage D smoke checks; not a quality benchmark."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ddl_text.codex_client import CodexClient, live_options
from ddl_text.provider import make_client
from ddl_text.config import Settings
from ddl_text.extraction import ExtractionAdapter, load_instructions
from ddl_text.pipeline import process_request
from ddl_text.render import render_text


CASE_IDS = ("absolute", "multiple", "no-date", "relative", "relative-no-base",
            "conflict", "postpone", "cancel-unknown", "no-task", "injection")


def signature(task):
    deadline = task["deadline"]
    # Do not score title wording; compare actionable count and time/change meaning.
    return (task["notice_type"], deadline["value"], deadline["precision"], deadline["kind"],
            deadline["status"], tuple(sorted(deadline["alternatives"])))


def assess(result, expected):
    problems = []
    if result["status"] != expected["status"]:
        problems.append("result_status")
    actual_signatures = sorted((signature(t) for t in result["tasks"]), key=repr)
    expected_signatures = sorted((signature(t) for t in expected["tasks"]), key=repr)
    if actual_signatures != expected_signatures:
        problems.append("task_count_or_time_or_notice")
    if any(t["deadline"]["status"] != "explicit" and not t["needs_review"] for t in result["tasks"]):
        problems.append("missing_review_flag")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Required: invoke paid API using synthetic records only")
    parser.add_argument("--cases", nargs="+", choices=CASE_IDS, default=list(CASE_IDS))
    parser.add_argument("--output", required=True, help="New report path; never overwrite an earlier run")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this command invokes the configured API")
    path = Path(args.output)
    if path.exists():
        parser.error("report already exists; choose a new path")
    options = live_options(ROOT / ".env")
    if not options["key"].strip():
        parser.error("missing local credential")
    settings = Settings(mode="real", model=options["model"], endpoint=options["endpoint"], timeout_seconds=120)
    client = make_client(options)
    adapter = ExtractionAdapter(client)
    captured = []
    generate = client.generate
    def capture(*arguments):
        response = generate(*arguments)
        captured.append(response)
        return response
    # This script only accepts fixed synthetic fixture IDs, never private files.
    if isinstance(client, CodexClient):
        client.generate = capture
    cases = json.loads((ROOT / "docs/examples/text-contract-v0.1.json").read_text(encoding="utf-8"))["cases"]
    version = (subprocess.run([client.executable, "--version"], capture_output=True, encoding="utf-8", timeout=10).stdout.strip()
               if isinstance(client, CodexClient) else None)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "model": settings.model, "provider": options["provider"],
              "codex_version": version, "python_version": platform.python_version(),
              "instructions_sha256": hashlib.sha256(load_instructions().encode("utf-8")).hexdigest(),
              "timeout_seconds": settings.timeout_seconds, "max_retries": 0,
              "synthetic_only": True, "usage_and_cost": "not collected", "cases": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the report before making requests; save each result immediately.
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for name in dict.fromkeys(args.cases):
        case = next(c for c in cases if c["case_id"] == name)
        started = time.monotonic()
        captured.clear()
        result = process_request(case["input"], adapter, settings)
        problems = assess(result, case["expected"])
        row = {"case_id": name, "elapsed_seconds": round(time.monotonic() - started, 2),
               "passed": not problems, "mismatches": problems, "result": result,
               "rendered_text": render_text(result, "real"), "synthetic_drafts": list(captured)}
        report["cases"].append(row)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("%s: %s (%.2fs) %s" % (name, "PASS" if not problems else "FAIL", row["elapsed_seconds"], problems), flush=True)
    return 0 if all(row["passed"] for row in report["cases"]) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
