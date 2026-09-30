"""Opt-in paid F3 classification smoke test using fixed synthetic notices only."""

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from ddl_text.adapters import AgentError
from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.contract_v02 import validate_result
from ddl_text.extraction import compile_drafts, load_instructions
from ddl_text.provider import make_client
from ddl_text.tests.classification_cases import CASES, matches, signature
from ddl_text.tests.teaching_section_cases import CASES as SECTION_CASES


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path, required=True)
    suites = {"classification": CASES, "teaching-sections": SECTION_CASES}
    parser.add_argument("--suite", choices=suites, default="classification")
    parser.add_argument("--cases", nargs="+")
    args = parser.parse_args()
    cases = suites[args.suite]
    if args.cases and set(args.cases) - {c['name'] for c in cases}:
        parser.error('--cases must name cases in the selected suite')
    if not args.live:
        parser.error("--live is required")
    if args.output.exists():
        parser.error("output already exists")
    options = live_options(args.env_file)
    client = make_client(options)
    settings = Settings(mode="real", model=options["model"], endpoint=options["endpoint"], timeout_seconds=90, max_retries=0)
    instructions = load_instructions()
    report = {"synthetic_only": True, "started_at": datetime.now(timezone.utc).isoformat(),
              "provider": options["provider"], "model": options["model"], "timeout_seconds": 90, "max_retries": 0,
              "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
              "instructions_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
              "scope": "Fixed smoke cases, not an independent accuracy benchmark", "suite": args.suite, "cases": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    for case in cases:
        if args.cases and case["name"] not in args.cases:
            continue
        row = dict(case, passed=False)
        started = time.monotonic()
        try:
            record = case["request"]["records"][0]
            raw = client.generate(instructions, json.dumps(record, ensure_ascii=False), settings, 0)
            row["model_draft"] = raw
            tasks = compile_drafts(raw, record)
            status = "success" if tasks else "no_tasks"
            result = {"contract_version": "0.2", "request_id": case["request"]["request_id"],
                      "status": status, "tasks": tasks, "errors": [], "record_results": [{
                          "input_id": record["input_id"], "status": status,
                          "task_ids": [t["candidate_id"] for t in tasks], "warnings": tasks.warnings}]}
            row["result"] = validate_result(case["request"], result)
            row["actual"] = [signature(t) for t in tasks]
            row["passed"] = row["result"] == result and matches(case, result)
        except AgentError as error:
            row["error_code"] = error.code
        except (ValueError, TypeError, KeyError):
            row["error_code"] = "invalid_result"
        row["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["cases"].append(row)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: row[k] for k in ("name", "passed", "elapsed_seconds")}), flush=True)
    return 0 if report["cases"] and all(row["passed"] for row in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
