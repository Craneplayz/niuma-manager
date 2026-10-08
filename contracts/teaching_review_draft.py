"""Executable review-policy proposal, NOT a production API or authenticator.

Only synthetic examples opt into source_verified. A future server must resolve
the account-bound snapshot itself before supplying that out-of-band fact.
"""

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "schemas/teaching-review-pair-v0.4-draft.schema.json").read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _day(value, zone):
    if "T" not in value and "t" not in value:
        return date.fromisoformat(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00")).astimezone(zone).date()


def validate_pair(pair):
    """Check standalone shape, references and cross-field consistency offline."""
    require(len(json.dumps(pair, ensure_ascii=False, allow_nan=False).encode("utf-8")) <= 1_000_000,
            "pair exceeds draft budget")
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(pair)
    record, assessment = pair["record"], pair["assessment"]
    require(record["input_id"] == assessment["input_id"], "candidate belongs to another record")
    zone = ZoneInfo(record["timezone"])
    refs = {ref["ref_id"]: ref for ref in record["references"]}
    require(len(refs) == len(record["references"]), "duplicate reference ID")
    require(not {"body", "source_metadata"}.intersection(refs), "reserved reference ID")
    require(bool(record["text"].strip()) or record["body_status"] == "missing", "empty body is not complete")
    require(record["body_status"] != "missing" or not record["text"], "missing body carries text")
    available = {"body": record["text"], "source_metadata": record["source_metadata_text"]}
    unread = set()
    for rid, ref in refs.items():
        if ref["read_state"] in {"complete", "partial"}:
            require(isinstance(ref["text"], str) and bool(ref["text"].strip()), "read reference needs captured text")
            available[rid] = ref["text"]
        else:
            require(ref["text"] is None, "unread reference cannot claim captured text")
        if ref["read_state"] != "complete":
            unread.add(rid)
    require(sum(map(len, available.values())) <= 100_000, "aggregate text exceeds draft budget")

    def check_evidence(evidence, allow_metadata=False):
        for item in evidence:
            require(item["part_id"] in available, "reference not captured in this record")
            require(allow_metadata or item["part_id"] != "source_metadata", "metadata cannot replace task quotation")
            require(item["quote"].strip() and item["quote"] in available[item["part_id"]], "quotation not present")

    check_evidence(assessment["action"]["evidence"])
    require(assessment["action"]["state"] != "supported" or bool(assessment["action"]["evidence"]),
            "supported action needs evidence")
    d = assessment["deadline"]
    check_evidence(d["evidence"])
    if d["derivation"] in {"unresolved", "not_provided"}:
        require(d["value"] is None and d["precision"] == "unknown" and d["anchor"] is None,
                "unknown deadline cannot carry a value or anchor")
    else:
        require(d["value"] is not None and d["precision"] in {"date", "datetime"} and d["evidence"],
                "known deadline needs a value, precision and evidence")
        is_datetime = "T" in d["value"] or "t" in d["value"]
        require(is_datetime == (d["precision"] == "datetime"), "deadline precision mismatch")
        if is_datetime:
            stamp = datetime.fromisoformat(d["value"].replace("Z", "+00:00").replace("z", "+00:00"))
            require(stamp.utcoffset() == stamp.astimezone(zone).utcoffset(), "deadline offset disagrees with timezone")
        if d["derivation"] == "absolute":
            require(d["anchor"] is None, "absolute date must not borrow an anchor")
        elif d["derivation"] in {"relative", "offset"}:
            require(d["anchor"] == "published_at", "relative dates require publication anchor")
        else:
            require(d["anchor"] in {"published_at", "collected_at", "course_term"}, "missing year requires anchor")
    relations = {r["ref_id"]: r for r in assessment["unread_relations"]}
    require(len(relations) == len(assessment["unread_relations"]), "duplicate relation")
    require(set(relations) == unread, "every unread reference needs exactly one assessment")
    for relation in relations.values():
        check_evidence(relation["evidence"])
        require(relation["role"] == "unknown" or bool(relation["evidence"]), "known relation needs evidence")
    term = record["course_term"]
    if term:
        start, end = date.fromisoformat(term["starts_on"]), date.fromisoformat(term["ends_on"])
        require(0 < (end - start).days <= 366, "term interval invalid or too broad")
        require(term["evidence"] and all(e["part_id"] == "source_metadata" for e in term["evidence"]),
                "term needs source metadata evidence")
        check_evidence(term["evidence"], allow_metadata=True)
    return pair


def _nearest_year(value, anchor):
    possibilities = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            possibilities.append(value.replace(year=year))
        except ValueError:
            pass
    return min(possibilities, key=lambda d: (abs((d - anchor).days), d < anchor)) if possibilities else None


def review_codes(pair, *, source_verified=False):
    """Return ONLY these draft gates' reasons, not a final auto-apply decision."""
    validate_pair(pair)
    require(type(source_verified) is bool, "source_verified must be an out-of-band boolean")
    r, a = pair["record"], pair["assessment"]
    d, term = a["deadline"], r["course_term"]
    zone = ZoneInfo(r["timezone"])
    codes = []
    if not source_verified:
        codes.append("source_unverified")
    if r["body_status"] != "complete":
        codes.append("body_incomplete")
    if a["action"]["state"] != "supported":
        codes.append("action_uncertain")
    field_parts = {item["part_id"] for field in (a["action"], d) for item in field["evidence"]}
    known_fields = [a["action"]] + ([d] if d["value"] is not None else [])
    body_supported = all(any(item["part_id"] == "body" for item in field["evidence"])
                         for field in known_fields)
    for relation in a["unread_relations"]:
        role = relation["role"]
        if role == "supplementary":
            # Captured text is valid evidence, but a partial reference cannot
            # exempt itself while supplying the task's action or deadline.
            if relation["ref_id"] in field_parts:
                role = "required"
            elif not body_supported or not all(item["part_id"] == "body" for item in relation["evidence"]):
                role = "unknown"
        if role != "supplementary":
            codes.append("unread_" + role + ":" + relation["ref_id"])
    if d["derivation"] == "unresolved":
        codes.append("deadline_unknown")
    elif d["derivation"] == "not_provided":
        if r["body_status"] != "complete" or a["unread_relations"]:
            codes.append("deadline_coverage_unknown")
    else:
        publication = r["published_at"]
        trusted_publication = source_verified and publication is not None and publication["basis"] == "source"
        if d["derivation"] in {"relative", "offset"}:
            if not trusted_publication or (d["derivation"] == "offset" and
                    not any(separator in publication["value"] for separator in ("T", "t"))):
                codes.append("publication_required")
        elif d["derivation"] == "missing_year":
            value = _day(d["value"], zone)
            if trusted_publication:
                if d["anchor"] != "published_at":
                    codes.append("publication_anchor_required")
                elif _nearest_year(value, _day(publication["value"], zone)) != value:
                    codes.append("year_anchor_mismatch")
            elif d["anchor"] == "course_term" and source_verified and term and term["applies_to"] == "current_assignment":
                start, end = date.fromisoformat(term["starts_on"]), date.fromisoformat(term["ends_on"])
                choices = []
                for year in range(start.year, end.year + 1):
                    try:
                        proposed = value.replace(year=year)
                        if start <= proposed <= end:
                            choices.append(proposed)
                    except ValueError:
                        pass
                if choices != [value]:
                    codes.append("term_date_unresolved")
            else:
                codes.append("original_age_unknown")
    if a["other_review_reasons"]:
        codes.append("other_review")
    return list(dict.fromkeys(codes))


def main():
    Draft202012Validator.check_schema(SCHEMA)
    cases = json.loads((ROOT / "examples/teaching-review-v0.4-draft-cases.json").read_text(encoding="utf-8"))["cases"]
    for case in cases:
        actual = review_codes(case["pair"], source_verified=case["source_verified"])
        require(actual == case["expected_review_codes"], "decision mismatch: " + case["name"])
    print("PASS:", len(cases), "synthetic teaching-policy cases; no runtime or model calls")


if __name__ == "__main__":
    main()
