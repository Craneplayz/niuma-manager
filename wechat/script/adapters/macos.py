"""macOS adapter for normalized, offline WeChat snapshots.

The live Windows database/key reader is intentionally not imported here.  A
snapshot is a JSON file or directory of JSON files with the same top-level
chat fields emitted by this exporter and a ``messages`` list.  A future native
macOS reader can implement this input format without changing the exporter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .base import BackendUnavailable


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackendUnavailable(f"无法读取 macOS 快照 {path}: {exc}") from exc


def _chat_documents(payload: Any, source: Path) -> tuple[dict[str, Any], list[Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("chats"), list):
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        return account, payload["chats"]
    if isinstance(payload, dict):
        return {}, [payload]
    if isinstance(payload, list):
        return {}, payload
    raise BackendUnavailable(f"快照 {source} 必须是对象、对象数组或包含 chats 的对象。")


def _message_timestamp(message: dict[str, Any]) -> int:
    try:
        timestamp = int(message.get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0
    return timestamp // 1000 if timestamp > 10_000_000_000 else timestamp


@dataclass
class MacOSSnapshotBackend:
    """Read canonical chat JSON without accessing the live WeChat process."""

    snapshot: Path

    def _paths(self) -> Iterable[Path]:
        if self.snapshot.is_file():
            return (self.snapshot,)
        if self.snapshot.is_dir():
            return sorted(self.snapshot.glob("*.json"))
        raise BackendUnavailable(f"macOS 快照路径不存在：{self.snapshot}")

    def load_chats(self) -> list[dict[str, Any]]:
        chats: list[dict[str, Any]] = []
        for path in self._paths():
            account, documents = _chat_documents(_read_json(path), path)
            for index, document in enumerate(documents, start=1):
                if not isinstance(document, dict):
                    raise BackendUnavailable(f"快照 {path} 的第 {index} 个会话不是对象。")
                messages = document.get("messages")
                if not isinstance(messages, list):
                    raise BackendUnavailable(f"快照 {path} 的会话缺少 messages 数组。")
                chat = dict(document)
                chat_account = chat.get("account")
                if not isinstance(chat_account, dict):
                    chat["account"] = dict(account)
                chat["chat_username"] = str(
                    chat.get("chat_username") or chat.get("chat_name") or path.stem
                )
                chat["chat_name"] = str(chat.get("chat_name") or chat["chat_username"])
                chat["chat_type"] = chat.get("chat_type") or (
                    "group" if chat["chat_username"].endswith("@chatroom") else "contact"
                )
                chats.append(chat)
        return chats

    def recent_chats(
        self,
        start_timestamp: int,
        end_timestamp: int,
        session_limit: int,
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for chat in self.load_chats():
            messages = []
            for message in chat["messages"]:
                if not isinstance(message, dict):
                    continue
                timestamp = _message_timestamp(message)
                if timestamp and not (start_timestamp <= timestamp <= end_timestamp):
                    continue
                messages.append(message)
            if not messages:
                continue
            normalized = dict(chat)
            normalized["messages"] = sorted(
                messages,
                key=_message_timestamp,
            )
            normalized["message_count"] = len(messages)
            selected.append(normalized)
            if len(selected) >= session_limit:
                break
        return selected

    @staticmethod
    def with_range(
        chat: dict[str, Any],
        *,
        start: datetime,
        end: datetime,
        days: float,
    ) -> dict[str, Any]:
        payload = dict(chat)
        payload.setdefault("schema_version", 1)
        payload["range"] = {
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"),
            "days": days,
        }
        payload["exported_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        payload["message_count"] = len(payload.get("messages", []))
        return payload
