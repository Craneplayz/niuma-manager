"""Check synthetic documentation examples, not agent behavior or model quality."""

import json
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs/examples/text-contract-v0.1.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_time(value, precision):
    if precision == "unknown":
        require(value is None, "Unknown time must have a null value")
    elif precision == "date":
        require(date.fromisoformat(value).isoformat() == value, "Invalid date")
    elif precision == "datetime":
        require("T" in value and datetime.fromisoformat(value).utcoffset() is not None,
                "Datetime must include an offset")
    else:
        raise ValueError(f"Unknown precision: {precision}")


def check_case(case):
    request, result = case["input"], case["expected"]
    require(request["contract_version"] == result["contract_version"] == "0.1",
            "Version mismatch")
    require(request["request_id"] == result["request_id"], "Request mismatch")
    records = {r["input_id"]: r for r in request["records"]}
    require(len(records) == len(request["records"]), "Duplicate input IDs")
    tasks = {t["candidate_id"]: t for t in result["tasks"]}
    require(len(tasks) == len(result["tasks"]), "Duplicate candidate IDs")
    outcomes = {r["input_id"]: r for r in result["record_results"]}
    require(len(outcomes) == len(result["record_results"]), "Duplicate outcomes")
    require(outcomes.keys() == records.keys(), "Missing input outcome")
    for record in records.values():
        check_time(record["published_at"]["value"], record["published_at"]["precision"])
        if record["text_quality"] in {"summary", "partial"}:
            require("incomplete_text" in outcomes[record["input_id"]]["warnings"],
                    "Missing text coverage warning")
    for task in tasks.values():
        require(task["input_id"] in records, "Unknown task input")
        require(bool(task["title"].strip()), "Empty title")
        record = records[task["input_id"]]
        available = {record["input_id"]: record}
        available.update({r["input_id"]: r for r in record["context"]})
        require(bool(task["sources"]) and bool(task["evidence"]), "Missing evidence/source")
        for source in task["sources"]:
            original = available[source["input_id"]]
            require(source["url"] == original["source_url"], "Invented source URL")
        for evidence in task["evidence"]:
            original = available[evidence["input_id"]]
            for segment in evidence["field"].split("."):
                original = original[segment]
            require(bool(evidence["quote"]) and evidence["quote"] in original,
                    "Evidence is not an exact input substring")
        deadline = task["deadline"]
        check_time(deadline["value"], deadline["precision"])
        require(task["needs_review"] == bool(task["review_reasons"]), "Review reason mismatch")
        if deadline["status"] in {"inferred", "unknown", "conflict"}:
            require(task["needs_review"], "Uncertainty must be visible")
        if deadline["status"] in {"unknown", "conflict"}:
            require(deadline["value"] is None, "Unresolved time cannot have a chosen value")
        if deadline["kind"] == "reference":
            require(deadline["status"] in {"inferred", "unknown", "conflict"}, "Reference time cannot be explicit")
        require(bool(deadline["alternatives"]) == (deadline["status"] == "conflict"),
                "Conflict alternatives mismatch")
    for input_id, outcome in outcomes.items():
        actual_ids = [t["candidate_id"] for t in result["tasks"] if t["input_id"] == input_id]
        require(outcome["task_ids"] == actual_ids, "Task index mismatch")
        has_error = any(e["input_id"] == input_id for e in result["errors"])
        expected = "failed" if has_error else "success" if actual_ids else "no_tasks"
        require(outcome["status"] == expected, "Record status mismatch")
        require(not has_error or not actual_ids, "Failed record emitted usable tasks")
    for error in result["errors"]:
        require(error["input_id"] in records, "Unknown error input")
    failed = sum(r["status"] == "failed" for r in outcomes.values())
    expected = ("failed" if failed == len(records) else "partial_success") if failed else (
        "success" if tasks else "no_tasks")
    require(result["status"] == expected, "Aggregate status mismatch")


def main():
    data = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    ids = [case["case_id"] for case in data["cases"]]
    require(len(ids) == len(set(ids)), "Duplicate case IDs")
    for case in data["cases"]:
        try:
            check_case(case)
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f'{case["case_id"]}: {error}') from error
    print(f"PASS: {len(ids)} synthetic contract examples; no model calls performed.")


if __name__ == "__main__":
    main()
