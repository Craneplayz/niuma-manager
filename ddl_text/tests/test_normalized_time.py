"""Structural checks for model proposals; these are not NLP accuracy tests."""

import unittest
from copy import deepcopy

from ddl_text.contracts import ContractError, normalize_record
from ddl_text.time_validation import validate_time


def proposal(quote, value=None, *, kind="deadline", derivation="absolute", anchor=None, basis="原文给出时间。"):
    return {"input_id": "m", "quote": quote, "kind": kind, "normalized": {
        "value": value, "precision": "datetime" if value and "T" in value else "date" if value else "unknown",
        "derivation": derivation, "anchor": anchor, "basis": basis}}


def record(**kwargs):
    return normalize_record(dict({"input_id": "m", "text": "合成通知",
        "published_at": {"value": "2026-12-31T10:00:00+08:00", "precision": "datetime", "basis": "source"},
        "timezone": "Asia/Shanghai", "timezone_basis": "source"}, **kwargs))


class NormalizedTimeTests(unittest.TestCase):
    def test_source_spelling_is_not_an_acceptance_gate(self):
        for quote in ("2026.10.11（23:59:30）", "二〇二六年十月十一日二十三时五十九分三十秒",
                      "October 11, 2026 at 11:59:30 PM", "2026年10月11日，最晚晚上11点59分30秒"):
            with self.subTest(quote=quote):
                actual = validate_time(proposal(quote, "2026-10-11T23:59:30+08:00"), record())
                self.assertEqual(actual["value"], "2026-10-11T23:59:30+08:00")
                self.assertEqual(actual["status"], "explicit")
                self.assertEqual(actual["raw"], quote)

    def test_invalid_standard_value_remains_reviewable(self):
        for value in ("2026-02-30", "2026-13-01", "2026-10-11T24:00:00+08:00",
                      "2026-10-11T23:59:60+08:00", "2026-10-11T12:00:00+08:60",
                      "2026-10-11T24:00:00Z", "2026-10-11T23:59:00", "tomorrow", "20261011"):
            with self.subTest(value=value):
                result = validate_time(proposal("时间原文", value), record())
                self.assertEqual((result["value"], result["status"]), (None, "unknown"))

    def test_bad_shape_is_protocol_error(self):
        for change in ({"precision": []}, {"anchor": "server_now"}, {"value": 123}, {"basis": ""}, {"extra": 1}):
            item = proposal("原文", "2026-10-11")
            item["normalized"].update(change)
            with self.subTest(change=change), self.assertRaises((ContractError, TypeError)):
                validate_time(item, record())

    def test_precision_and_reference_role(self):
        item = proposal("十月十一日", "2026-10-11")
        result = validate_time(item, record())
        self.assertEqual((result["value"], result["precision"]), ("2026-10-11", "date"))
        item["kind"] = "reference"
        self.assertEqual(validate_time(item, record())["status"], "inferred")
        self.assertEqual(item["normalized"]["value"], "2026-10-11")

    def test_offset_and_zone_must_agree(self):
        for zone, value in (("Asia/Shanghai", "2026-10-11T10:00:00Z"),
                            (None, "2026-10-11T10:00:00+08:00"),
                            ("America/New_York", "2026-03-08T02:30:00-05:00"),
                            ("America/New_York", "2026-11-01T01:30:00-04:00")):
            with self.subTest(zone=zone, value=value):
                r = record(timezone=zone, timezone_basis="source" if zone else "unknown")
                self.assertIsNone(validate_time(proposal("原文时刻", value), r)["value"])
        self.assertEqual(validate_time(proposal("某日", "2026-10-11"), record(timezone=None, timezone_basis="unknown"))["value"], "2026-10-11")

    def test_relative_uses_publication_not_collection(self):
        item = proposal("明天十八点", "2027-01-01T18:00:00+08:00", derivation="relative", anchor="published_at")
        r = record(collected_at="2027-02-01T00:00:00+08:00")
        self.assertEqual(validate_time(item, r)["status"], "explicit")
        r["published_at"]["basis"] = "upstream_unverified"
        self.assertIsNone(validate_time(item, r)["value"])
        item["normalized"]["anchor"] = "collected_at"
        self.assertIsNone(validate_time(item, r)["value"])

    def test_hour_offset_requires_time_precision(self):
        item = proposal("再过一天半", "2027-01-01T22:00:00+08:00", derivation="offset", anchor="published_at")
        self.assertIsNotNone(validate_time(item, record())["value"])
        r = record(published_at={"value": "2026-12-31", "precision": "date", "basis": "source"})
        self.assertIsNone(validate_time(item, r)["value"])

    def test_missing_year_policy_uses_structured_date_not_quote(self):
        for publication, value in (("2026-12-31", "2027-01-05"), ("2027-01-02", "2026-12-30"),
                                   ("2026-09-25", "2026-09-20"), ("2027-12-31", "2028-02-29")):
            with self.subTest(value=value):
                r = record(published_at={"value": publication, "precision": "date", "basis": "source"})
                item = proposal("任意月日书写方式", value, derivation="missing_year", anchor="published_at")
                result = validate_time(item, r)
                self.assertEqual((result["value"], result["status"]), (value, "explicit"))
                self.assertIn(publication, result["basis"])
        item = proposal("一月五日", "2026-01-05", derivation="missing_year", anchor="published_at")
        self.assertIsNone(validate_time(item, record())["value"])

    def test_missing_year_fallback_does_not_override_publication(self):
        item = proposal("九月三十日", "2026-09-30", derivation="missing_year", anchor="collected_at")
        r = record(collected_at="2026-09-25T12:00:00+08:00")
        self.assertIsNone(validate_time(item, r)["value"])
        r["published_at"] = {"value": None, "precision": "unknown", "basis": "unknown"}
        self.assertEqual(validate_time(item, r)["status"], "explicit")
        r["collected_at"] = None
        self.assertIsNone(validate_time(item, r)["value"])

    def test_anchor_day_respects_local_timezone(self):
        r = record(published_at={"value": "2026-12-31T20:00:00Z", "precision": "datetime", "basis": "source"})
        item = proposal("一月二号", "2027-01-02", derivation="missing_year", anchor="published_at")
        self.assertIn("2027-01-01", validate_time(item, r)["basis"])

    def test_inconsistent_proposals_do_not_gain_certainty(self):
        cases = [proposal("模糊日期", "2026-10-11", derivation="unresolved"),
                 proposal("某日", None),
                 proposal("2026年十月十一日", "2026-10-11", anchor="published_at"),
                 proposal("尽快", "2026-10-11", kind="urgency")]
        for item in cases:
            with self.subTest(item=item):
                self.assertIsNone(validate_time(item, record())["value"])
        unknown = proposal("方便的时候", derivation="unresolved", basis="未给出确定日期。")
        original = deepcopy(unknown)
        self.assertEqual(validate_time(unknown, record())["basis"], "未给出确定日期。")
        self.assertEqual(unknown, original)


if __name__ == "__main__":
    unittest.main()
