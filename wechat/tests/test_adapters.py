"""Offline tests for platform selection and the macOS snapshot adapter."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wechat.script.adapters import build_backend
from wechat.script.adapters.base import BackendUnavailable
from wechat.script.adapters.macos import MacOSSnapshotBackend
from wechat.script.extract_data import export_snapshot_chats


class MacOSSnapshotBackendTest(unittest.TestCase):
    def test_reads_and_filters_normalized_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "snapshot.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "account": {"username": "wxid_me", "nickname": "我"},
                        "chats": [
                            {
                                "chat_name": "课程群",
                                "chat_username": "course@chatroom",
                                "messages": [
                                    {"timestamp": 100, "content": "old"},
                                    {"timestamp": 150, "content": "keep"},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            backend = MacOSSnapshotBackend(snapshot)
            chats = backend.recent_chats(120, 180, 10)

            self.assertEqual(len(chats), 1)
            self.assertEqual(chats[0]["account"]["username"], "wxid_me")
            self.assertEqual([m["content"] for m in chats[0]["messages"]], ["keep"])

    def test_build_backend_requires_snapshot_on_macos(self):
        with self.assertRaises(BackendUnavailable):
            build_backend("macos")

    def test_snapshot_export_writes_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "chat.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "chat_name": "联系人",
                        "chat_username": "wxid_contact",
                        "messages": [{"timestamp": 100, "type_code": 1, "content": "你好"}],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "out"
            backend = build_backend("macos", snapshot=snapshot)
            start = datetime.fromtimestamp(50, tz=timezone.utc)
            end = datetime.fromtimestamp(150, tz=timezone.utc)
            result = export_snapshot_chats(
                backend,
                output_dir=output,
                start=start,
                now=end,
                days=1,
                session_limit=10,
            )

            payload = json.loads((output / "联系人.json").read_text(encoding="utf-8"))
            self.assertEqual(result, 0)
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["message_count"], 1)


if __name__ == "__main__":
    unittest.main()
