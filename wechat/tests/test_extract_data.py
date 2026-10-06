"""Offline tests for the WeChat exporter helpers."""

import contextlib
import io
import unittest

from wechat.script.extract_data import (
    build_parser,
    extract_urls,
    human_summary,
    normalize_timestamp,
    safe_name,
    strip_group_prefix,
    unique_chat_stem,
)


class ExtractDataHelpersTest(unittest.TestCase):
    def test_safe_name_replaces_windows_unsafe_values(self):
        self.assertEqual(safe_name('  A<>:"/\\|?*  '), "A_________")
        self.assertEqual(safe_name("CON"), "_CON")
        self.assertEqual(safe_name("   "), "未命名会话")

    def test_unique_chat_stem_disambiguates_duplicate_names(self):
        used = set()
        self.assertEqual(unique_chat_stem("课程群", "first@chatroom", used), "课程群")
        self.assertEqual(
            unique_chat_stem("课程群", "second@chatroom", used), "课程群__chatroom"
        )
        self.assertEqual(
            unique_chat_stem("课程群", "third@chatroom", used), "课程群__chatroom_2"
        )

    def test_normalize_timestamp_accepts_seconds_and_milliseconds(self):
        self.assertEqual(normalize_timestamp(1_700_000_000), 1_700_000_000)
        self.assertEqual(normalize_timestamp(1_700_000_000_000), 1_700_000_000)
        self.assertEqual(normalize_timestamp("invalid"), 0)

    def test_extract_urls_reads_xml_and_plain_text_without_duplicates(self):
        text = '<url>https://example.com/a</url> see https://example.com/a, http://example.org/b'
        self.assertEqual(
            extract_urls(text), ["https://example.com/a", "http://example.org/b"]
        )

    def test_group_prefix_is_removed_before_message_summary(self):
        sender, body = strip_group_prefix("wxid_sender:\n请明天交报告")
        self.assertEqual(sender, "wxid_sender")
        self.assertEqual(body, "请明天交报告")

    def test_human_summary_preserves_structured_location_fields(self):
        content, details = human_summary(
            "位置", 48, '<location x="39.9" y="116.4" label="北京" />'
        )
        self.assertEqual(content, "[位置] 北京")
        self.assertEqual(details["latitude"], "39.9")
        self.assertEqual(details["longitude"], "116.4")

    def test_parser_defaults_match_documented_entrypoint(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.platform, "auto")
        self.assertEqual(args.days, 7.0)
        self.assertEqual(args.session_limit, 10000)
        self.assertEqual(args.selected_chats, [])
        self.assertFalse(args.skip_media)

    def test_parser_rejects_unknown_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["--unknown"])


if __name__ == "__main__":
    unittest.main()
