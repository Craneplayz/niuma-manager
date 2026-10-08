"""Reject forged draft relations and keep independent review gates intact."""

import json
import unittest
from copy import deepcopy
from pathlib import Path

from jsonschema import ValidationError

from contracts.teaching_review_draft import SCHEMA, review_codes, validate_pair


CASES = json.loads((Path(__file__).resolve().parents[1] / "examples/teaching-review-v0.4-draft-cases.json").read_text(encoding="utf-8"))["cases"]


def sample(name="unread-supplement"):
    return deepcopy(next(c["pair"] for c in CASES if c["name"] == name))


class TeachingDraftTests(unittest.TestCase):
    def test_shared_decision_cases(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(review_codes(case["pair"], source_verified=case["source_verified"]),
                                 case["expected_review_codes"])

    def test_schema_is_standalone_and_rejects_empty_root(self):
        from jsonschema import Draft202012Validator, FormatChecker
        Draft202012Validator.check_schema(SCHEMA)
        validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
        with self.assertRaises(ValidationError):
            validator.validate({})
        for case in CASES:
            validator.validate(case["pair"])
        def walk(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    self.assertTrue(value["$ref"].startswith("#/$defs/"))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(SCHEMA)

    def test_wire_cannot_claim_source_authentication(self):
        pair = sample()
        self.assertIn("source_unverified", review_codes(pair))
        pair["record"]["source_verified"] = True
        with self.assertRaises(ValidationError):
            validate_pair(pair)

    def test_unread_reference_cannot_be_omitted_or_duplicated(self):
        for change in ("omit", "duplicate", "foreign"):
            pair = sample()
            rows = pair["assessment"]["unread_relations"]
            if change == "omit":
                rows.clear()
            elif change == "duplicate":
                rows.append(deepcopy(rows[0]))
            else:
                rows[0]["ref_id"] = "other-record-file"
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_pair(pair)

    def test_foreign_and_invented_evidence_rejected(self):
        for part, quote in (("extra", "not read"), ("body", "invented"), ("other-record", "第二次作业")):
            pair = sample()
            pair["assessment"]["action"]["evidence"] = [{"part_id": part, "quote": quote}]
            with self.subTest(part=part), self.assertRaises(ValueError):
                validate_pair(pair)

    def test_source_metadata_cannot_replace_action_evidence(self):
        pair = sample()
        pair["assessment"]["action"]["evidence"] = [{"part_id": "source_metadata", "quote": "当前布置的作业"}]
        with self.assertRaises(ValueError):
            validate_pair(pair)

    def test_identity_read_status_and_duplicate_ids_are_checked(self):
        for change in ("record", "duplicate", "unread-text", "read-without-text", "reserved"):
            pair = sample()
            ref = pair["record"]["references"][0]
            if change == "record":
                pair["assessment"]["input_id"] = "other-record"
            elif change == "duplicate":
                pair["record"]["references"].append(deepcopy(ref))
            elif change == "unread-text":
                ref["text"] = "fake read"
            elif change == "read-without-text":
                ref["read_state"] = "complete"
            else:
                ref["ref_id"] = "body"
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_pair(pair)

    def test_invalid_dates_precision_and_term_interval_are_rejected(self):
        for change in ("calendar", "precision", "reversed", "too-broad", "evidence"):
            pair = sample("yearless-current-assignment-term")
            if change == "calendar":
                pair["assessment"]["deadline"]["value"] = "2026-02-30"
            elif change == "precision":
                pair["assessment"]["deadline"]["precision"] = "datetime"
            elif change == "reversed":
                pair["record"]["course_term"]["ends_on"] = "2026-08-01"
            elif change == "too-broad":
                pair["record"]["course_term"]["ends_on"] = "2029-01-01"
            else:
                pair["record"]["course_term"]["evidence"] = []
            with self.subTest(change=change), self.assertRaises((ValueError, ValidationError)):
                validate_pair(pair)

    def test_independent_uncertainty_survives_good_source_coverage(self):
        pair = sample()
        pair["assessment"]["action"]["state"] = "uncertain"
        pair["assessment"]["other_review_reasons"] = ["正文与学期范围冲突。"]
        self.assertEqual(review_codes(pair, source_verified=True), ["action_uncertain", "other_review"])

    def test_partial_reference_cannot_exempt_itself(self):
        pair = sample()
        pair["record"]["text"] = "第二次作业的要求和截止日期见附件。"
        pair["record"]["references"][0].update(
            read_state="partial", text="完成练习1至3，2026年10月23日截止。本附件仅供参考。")
        pair["assessment"]["action"]["evidence"] = [{"part_id": "extra", "quote": "完成练习1至3"}]
        pair["assessment"]["deadline"]["evidence"] = [{"part_id": "extra", "quote": "2026年10月23日截止"}]
        pair["assessment"]["unread_relations"][0]["evidence"] = [
            {"part_id": "extra", "quote": "本附件仅供参考。"}]
        before = deepcopy(pair)
        validate_pair(pair)  # Captured quotes are valid; the exemption is not.
        self.assertEqual(review_codes(pair, source_verified=True), ["unread_required:extra"])
        self.assertEqual(pair, before)

    def test_body_relation_does_not_hide_partial_field_support(self):
        for field in ("action", "deadline"):
            for keep_body in (False, True):
                pair = sample()
                pair["record"]["references"][0].update(read_state="partial", text=pair["record"]["text"])
                evidence = pair["assessment"][field]["evidence"]
                quote = dict(evidence[0], part_id="extra")
                evidence[:] = (evidence if keep_body else []) + [quote]
                with self.subTest(field=field, keep_body=keep_body):
                    self.assertEqual(review_codes(pair, source_verified=True), ["unread_required:extra"])

    def test_supplementary_relation_requires_only_body_evidence(self):
        for state in ("complete", "partial"):
            for keep_body in (False, True):
                pair = sample()
                pair["record"]["references"].append(dict(ref_id="other", kind="attachment", label="其他说明",
                    read_state=state, text="附加练习仅供参考。"))
                if state == "partial":
                    pair["assessment"]["unread_relations"].append(dict(ref_id="other", role="unknown", evidence=[]))
                evidence = pair["assessment"]["unread_relations"][0]["evidence"]
                evidence[:] = (evidence if keep_body else []) + [dict(part_id="other", quote="附加练习仅供参考。")]
                expected = ["unread_unknown:extra"] + (["unread_unknown:other"] if state == "partial" else [])
                with self.subTest(state=state, keep_body=keep_body):
                    self.assertEqual(review_codes(pair, source_verified=True), expected)

    def test_complete_reference_alone_does_not_prove_body_support(self):
        for field in ("action", "deadline"):
            pair = sample()
            pair["record"]["references"].append(dict(ref_id="read-file", kind="attachment", label="已读材料",
                read_state="complete", text=pair["record"]["text"]))
            pair["assessment"][field]["evidence"][0]["part_id"] = "read-file"
            with self.subTest(field=field):
                self.assertEqual(review_codes(pair, source_verified=True), ["unread_unknown:extra"])

    def test_independent_body_support_allows_supplement_in_every_unread_state(self):
        for state in ("unread", "partial", "failed", "unsupported"):
            pair = sample()
            pair["record"]["references"][0].update(read_state=state,
                text="补充练习的参考思路。" if state == "partial" else None)
            with self.subTest(state=state):
                self.assertEqual(review_codes(pair, source_verified=True), [])

    def test_partial_field_dependency_is_assessed_per_reference(self):
        pair = sample()
        pair["record"]["references"][0].update(read_state="partial", text=pair["record"]["text"])
        pair["record"]["text"] += "其他讲义仅供参考。"
        pair["record"]["references"].append(dict(ref_id="other", kind="attachment", label="其他讲义",
            read_state="unread", text=None))
        pair["assessment"]["unread_relations"].append(dict(ref_id="other", role="supplementary",
            evidence=[dict(part_id="body", quote="其他讲义仅供参考。")]))
        evidence = pair["assessment"]["deadline"]["evidence"]
        evidence.append(dict(evidence[0], part_id="extra"))
        self.assertEqual(review_codes(pair, source_verified=True), ["unread_required:extra"])

    def test_trusted_publication_takes_priority_over_term(self):
        pair = sample("yearless-current-assignment-term")
        pair["record"]["published_at"] = {"value": "2023-10-01", "basis": "source"}
        self.assertEqual(review_codes(pair, source_verified=True), ["publication_anchor_required"])

    def test_duplicate_month_day_in_term_is_not_guessed(self):
        pair = sample("yearless-current-assignment-term")
        pair["record"]["course_term"].update(starts_on="2026-10-23", ends_on="2027-10-23")
        self.assertEqual(review_codes(pair, source_verified=True), ["term_date_unresolved"])

    def test_explicit_year_is_not_constrained_to_term(self):
        pair = sample("yearless-current-assignment-term")
        pair["record"]["text"] += "明确说明截止为2028年10月23日。"
        pair["assessment"]["deadline"].update(value="2028-10-23", derivation="absolute", anchor=None,
            evidence=[{"part_id": "body", "quote": "截止为2028年10月23日"}])
        self.assertEqual(review_codes(pair, source_verified=True), [])

    def test_unknown_deadline_cannot_claim_a_value(self):
        pair = sample("unknown-deadline")
        pair["assessment"]["deadline"]["value"] = "2026-10-23"
        with self.assertRaises(ValueError):
            validate_pair(pair)

    def test_input_limits_apply_to_total_text_and_unexpected_fields(self):
        pair = sample("explicit-complete")
        pair["record"]["text"] += "甲" * 99_950
        with self.assertRaises(ValueError):
            validate_pair(pair)
        pair = sample()
        pair["record"]["input_id"] = "x" * 257
        with self.assertRaises(ValidationError):
            validate_pair(pair)
        pair = sample()
        pair["assessment"]["needs_review"] = False
        with self.assertRaises(ValidationError):
            validate_pair(pair)

    def test_timezone_offset_and_offset_publication_precision(self):
        pair = sample("explicit-complete")
        pair["record"]["text"] += "2026年10月23日18点前交。"
        pair["assessment"]["deadline"].update(value="2026-10-23T18:00:00+08:00", precision="datetime",
            evidence=[{"part_id": "body", "quote": "2026年10月23日18点前交"}])
        self.assertEqual(review_codes(pair, source_verified=True), [])
        pair["assessment"]["deadline"]["value"] = "2026-10-23T18:00:00+00:00"
        with self.assertRaises(ValueError):
            validate_pair(pair)
        pair = sample("term-cannot-anchor-tomorrow")
        pair["record"]["published_at"] = {"value": "2026-10-06", "basis": "source"}
        pair["assessment"]["deadline"]["derivation"] = "offset"
        self.assertEqual(review_codes(pair, source_verified=True), ["publication_required"])


if __name__ == "__main__":
    unittest.main()
