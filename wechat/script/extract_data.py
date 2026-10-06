# -*- coding: utf-8 -*-
"""Export WeChat messages through the selected platform adapter.

Windows uses ``wechatauto-replica`` (import name: ``wechatauto``) to read
WeChat 4.x local databases.  macOS consumes normalized JSON snapshots.  By
default the script exports the last seven days of messages, one human-readable
JSON file per active contact/group, and restores locally available Windows
media into ``data/assets``.

Run from the project root with the requested conda environment::

    conda run -n wechat-export python wechat/script/extract_data.py

The code is deliberately read-only with respect to WeChat: it reads/decrypts
the local databases and copies available attachments, but does not send a
message or operate the WeChat UI.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

try:  # Supports both ``python -m wechat.script...`` and direct script execution.
    from .adapters import BackendUnavailable, build_backend
    from .adapters.macos import MacOSSnapshotBackend
except ImportError:  # pragma: no cover - exercised by the documented CLI path.
    from adapters import BackendUnavailable, build_backend  # type: ignore
    from adapters.macos import MacOSSnapshotBackend  # type: ignore


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data"
# Keep decrypted database caches outside the project deliverable.  They are
# regenerated automatically when the cache is absent and can be large.
DEFAULT_WORK_DIR = Path(tempfile.gettempdir()) / "wechatauto_export"

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_FILENAME_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# Kept as aliases for callers that imported the old names.
WINDOWS_INVALID_CHARS = INVALID_FILENAME_CHARS
WINDOWS_RESERVED_NAMES = RESERVED_FILENAME_NAMES
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
GROUP_PREFIX_RE = re.compile(
    r"^(?P<username>(?:wxid_[0-9A-Za-z_-]+)|(?:[^:\r\n]{1,160}@chatroom))"
    r":(?:\r?\n|\s)*"
)
MEDIA_BASE_TYPES = {3: "image", 34: "voice", 43: "video", 49: "file"}


def configure_console() -> None:
    """Keep Chinese names/content readable when redirected on Windows."""

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass


def prepare_comtypes_cache() -> None:
    """Compatibility wrapper for callers of the pre-adapter helper."""

    try:
        from .adapters.windows import prepare_comtypes_cache as prepare
    except ImportError:  # pragma: no cover - direct script compatibility.
        from adapters.windows import prepare_comtypes_cache as prepare  # type: ignore

    prepare(PROJECT_ROOT)


def import_wechat_packages():
    """Compatibility wrapper for callers of the pre-adapter helper."""

    try:
        from .adapters.windows import import_wechat_packages as import_packages
    except ImportError:  # pragma: no cover - direct script compatibility.
        from adapters.windows import import_wechat_packages as import_packages  # type: ignore

    return import_packages(PROJECT_ROOT)


def safe_name(value: str, fallback: str = "未命名会话", max_length: int = 120) -> str:
    """Return a readable filename component valid on Windows and macOS."""

    value = INVALID_FILENAME_CHARS.sub("_", (value or "").strip())
    value = re.sub(r"\s+", " ", value).rstrip(" .")
    if not value:
        value = fallback
    if value.upper() in RESERVED_FILENAME_NAMES:
        value = f"_{value}"
    return value[:max_length].rstrip(" .") or fallback


def unique_chat_stem(name: str, username: str, used: set[str]) -> str:
    stem = safe_name(name)
    key = stem.casefold()
    if key not in used:
        used.add(key)
        return stem
    suffix_source = re.sub(r"[^0-9A-Za-z]", "", username)[-8:] or "duplicate"
    candidate = safe_name(f"{stem}__{suffix_source}")
    serial = 2
    while candidate.casefold() in used:
        candidate = safe_name(f"{stem}__{suffix_source}_{serial}")
        serial += 1
    used.add(candidate.casefold())
    return candidate


def normalize_timestamp(value: Any) -> int:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return 0
    # Defensive support for millisecond timestamps.
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return timestamp


def iso_time(timestamp: int, local_timezone) -> str:
    return datetime.fromtimestamp(timestamp, tz=local_timezone).isoformat(
        timespec="seconds"
    )


def base_message_type(type_code: Any) -> int:
    try:
        code = int(type_code)
    except (TypeError, ValueError):
        return 0
    if code > 0xFFFF:
        base = code & 0xFFFFFFFF
        # Some composite values keep the real type in the low byte.
        if base not in {1, 3, 34, 43, 47, 48, 49, 50, 10000, 11000}:
            low_byte = base & 0xFF
            if low_byte in {1, 3, 34, 43, 47, 48, 49, 50}:
                return low_byte
        return base
    return code


def decode_blob(db, blob: Any, message_type: Any) -> str:
    if blob is None:
        return ""
    if isinstance(blob, str):
        return blob.strip("\x00")
    if isinstance(blob, (bytes, bytearray, memoryview)):
        raw = bytes(blob)
        try:
            return db._friendly_content(raw, message_type).strip("\x00")
        except Exception:
            return raw.decode("utf-8", "replace").strip("\x00")
    return str(blob)


def decode_message_content(db, row: dict[str, Any]) -> str:
    message_type = db._msg_type_name(row.get("local_type", 0))
    content = decode_blob(db, row.get("message_content"), message_type).strip()
    placeholder = f"[{message_type}]"
    if not content or content == placeholder:
        compressed = decode_blob(db, row.get("compress_content"), message_type).strip()
        if compressed and compressed != placeholder:
            content = compressed
    return content or placeholder


def strip_group_prefix(text: str) -> tuple[str, str]:
    match = GROUP_PREFIX_RE.match(text or "")
    if not match:
        return "", text
    return match.group("username"), text[match.end() :]


def clean_xml_value(value: str) -> str:
    value = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", value.strip(), flags=re.S)
    value = html.unescape(value)
    return value.replace("\x00", "").strip()


def xml_tag(text: str, tag: str) -> str:
    match = re.search(
        rf"<{re.escape(tag)}(?:\s[^>]*)?>(.*?)</{re.escape(tag)}>",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    return clean_xml_value(match.group(1)) if match else ""


def xml_attribute(text: str, element: str, attribute: str) -> str:
    match = re.search(
        rf"<{re.escape(element)}\b[^>]*\b{re.escape(attribute)}\s*=\s*"
        r"(?:\"([^\"]*)\"|'([^']*)')",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return clean_xml_value(match.group(1) if match.group(1) is not None else match.group(2))


def extract_urls(text: str) -> list[str]:
    candidates: list[str] = []
    for tag in (
        "url",
        "lowurl",
        "thumburl",
        "cdnurl",
        "externurl",
        "attachurl",
        "weburl",
    ):
        candidates.extend(
            clean_xml_value(value)
            for value in re.findall(
                rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>",
                text or "",
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
    candidates.extend(clean_xml_value(value) for value in URL_RE.findall(text or ""))

    output: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        # A tag can contain additional XML; retain only actual HTTP(S) matches.
        matches = URL_RE.findall(value)
        if not matches and value.lower().startswith(("http://", "https://")):
            matches = [value]
        for url in matches:
            url = html.unescape(url).rstrip(".,;，。；)]}>")
            if url and url not in seen:
                seen.add(url)
                output.append(url)
    return output


def human_summary(message_type: str, type_code: int, text: str) -> tuple[str, dict[str, Any]]:
    """Turn XML/container messages into concise text plus structured fields."""

    base_type = base_message_type(type_code)
    details: dict[str, Any] = {}
    stripped = (text or "").lstrip("\ufeff").strip()

    if base_type == 1:
        return stripped, details
    if base_type == 3:
        return "[图片]", details
    if base_type == 34:
        return "[语音]", details
    if base_type == 43:
        return "[视频]", details
    if base_type in (47, 11000):
        md5 = xml_attribute(stripped, "emoji", "md5")
        if md5:
            details["md5"] = md5
        return "[动画表情]", details
    if base_type == 48:
        label = xml_attribute(stripped, "location", "label")
        point_name = xml_attribute(stripped, "location", "poiname")
        latitude = (
            xml_attribute(stripped, "location", "x")
            or xml_attribute(stripped, "location", "latitude")
        )
        longitude = (
            xml_attribute(stripped, "location", "y")
            or xml_attribute(stripped, "location", "longitude")
        )
        details.update(
            {
                key: value
                for key, value in {
                    "label": label,
                    "point_name": point_name,
                    "latitude": latitude,
                    "longitude": longitude,
                }.items()
                if value
            }
        )
        location_name = point_name or label
        return f"[位置] {location_name}".strip(), details
    if base_type == 49:
        title = xml_tag(stripped, "title")
        description = xml_tag(stripped, "des")
        app_type = xml_tag(stripped, "type")
        filename = xml_tag(stripped, "fileext")
        details.update(
            {
                key: value
                for key, value in {
                    "title": title,
                    "description": description,
                    "app_message_type": app_type,
                    "file_extension": filename,
                }.items()
                if value
            }
        )
        label = "文件" if app_type == "6" else "链接/卡片"
        body = " — ".join(part for part in (title, description) if part)
        return f"[{label}] {body}".strip(), details
    if base_type == 50:
        body = xml_tag(stripped, "msg") or xml_tag(stripped, "content")
        return f"[音视频通话] {body or stripped}".strip(), details
    if base_type == 10000:
        body = xml_tag(stripped, "content") or re.sub(r"<[^>]+>", " ", stripped)
        body = " ".join(html.unescape(body).split())
        return f"[系统消息] {body}".strip(), details

    if stripped.startswith("<"):
        title = xml_tag(stripped, "title")
        description = xml_tag(stripped, "des")
        compact = " — ".join(part for part in (title, description) if part)
        if compact:
            return f"[{message_type}] {compact}", details
        compact = " ".join(re.sub(r"<[^>]+>", " ", stripped).split())
        return compact or f"[{message_type}]", details
    return stripped or f"[{message_type}]", details


def add_remote_assets(urls: Iterable[str]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "url",
            "url": url,
            "href": url,
            "available": True,
        }
        for url in urls
    ]


def local_asset(path: Path, output_dir: Path, kind: str, **extra: Any) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(output_dir.resolve()).as_posix()
    except ValueError:
        relative = None
    asset: dict[str, Any] = {
        "kind": kind,
        "path": str(resolved),
        "relative_path": relative,
        "href": resolved.as_uri(),
        "available": resolved.exists(),
    }
    asset.update({key: value for key, value in extra.items() if value is not None})
    return asset


def copy_with_unique_name(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    candidate = destination
    serial = 2
    while candidate.exists():
        try:
            if source.samefile(candidate):
                return candidate
        except (OSError, FileNotFoundError):
            pass
        candidate = destination.with_name(
            f"{destination.stem}_{serial}{destination.suffix}"
        )
        serial += 1
    shutil.copy2(source, candidate)
    return candidate


def media_prefix(row: dict[str, Any]) -> str:
    timestamp = normalize_timestamp(row.get("create_time"))
    identity = row.get("server_id") or row.get("sort_seq") or row.get("local_id")
    return safe_name(f"{timestamp}_{identity}_{row.get('local_id', 0)}", max_length=80)


def export_image(media, row: dict[str, Any], chat_user: str, asset_dir: Path, output_dir: Path):
    md5 = media._img_md5(
        {
            "packed_info": row.get("packed_info_data"),
            "content": row.get("message_content"),
        }
    )
    if not md5:
        return [], "消息资源中没有图片 MD5"
    source = media._find_dat(chat_user, md5, row.get("create_time"))
    thumbnail = False
    if not source:
        source = media._find_dat(
            chat_user, md5, row.get("create_time"), thumbnail=True
        )
        thumbnail = bool(source)
    if not source:
        return [], "本地微信缓存中未找到图片（可能未下载或已清理）"

    source_path = Path(source)
    assets = [
        local_asset(
            source_path,
            output_dir,
            "wechat_encrypted_image_cache",
            encrypted=True,
            md5=md5,
        )
    ]
    try:
        # Do not start the library's 120-second interactive AES-key monitor.
        # A cached/config-derived key is enough for unattended export; when it
        # is unavailable the encrypted cache path above is still useful and is
        # recorded in the JSON instead of blocking the whole export.
        aes_key = None
        xor_key = None
        try:
            cached = media._load_persisted_key()
            if cached:
                aes_key = cached
        except Exception:
            pass
        try:
            derived = media._derive_cfg_key()
            if derived:
                aes_key, xor_key = derived
        except Exception:
            pass
        if not aes_key:
            return assets, "未找到已缓存的图片密钥，已保留加密缓存地址"
        data = media.decrypt_image(str(source_path), aes_key=aes_key, xor_key=xor_key)
        extension = ".img"
        if data[:3] == b"\xff\xd8\xff":
            extension = ".jpg"
        elif data[:4] == b"\x89PNG":
            extension = ".png"
        elif data[:3] == b"GIF":
            extension = ".gif"
        elif data[:4] == b"RIFF":
            extension = ".webp"
        elif data[:4] == b"wxgf":
            converted = media._wxgf_to_jpg(data)
            if converted is not None:
                data = converted
                extension = ".jpg"
            else:
                extension = ".wxgf"
        suffix = "_thumb" if thumbnail else ""
        destination = asset_dir / f"{media_prefix(row)}{suffix}{extension}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        assets.insert(
            0,
            local_asset(
                destination,
                output_dir,
                "image",
                thumbnail=thumbnail,
                md5=md5,
            ),
        )
        return assets, None
    except Exception as exc:  # Media recovery is best-effort.
        return assets, f"图片解密失败：{type(exc).__name__}: {exc}"


def export_voice(db, row: dict[str, Any], chat_user: str, asset_dir: Path, output_dir: Path):
    server_id = row.get("server_id")
    if not server_id:
        return [], "语音消息没有 server_id"
    for rel, path, _ in db._db_files:
        if not os.path.basename(path).startswith("media_"):
            continue
        conn = db._open(rel)
        try:
            chat_row = conn.execute(
                "SELECT rowid FROM Name2Id WHERE user_name=?", (chat_user,)
            ).fetchone()
            if not chat_row:
                continue
            voice = conn.execute(
                "SELECT voice_data FROM VoiceInfo "
                "WHERE chat_name_id=? AND svr_id=? "
                "ORDER BY create_time DESC LIMIT 1",
                (chat_row[0], server_id),
            ).fetchone()
        finally:
            conn.close()
        if voice and voice["voice_data"]:
            destination = asset_dir / f"{media_prefix(row)}.silk"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(voice["voice_data"])
            return [local_asset(destination, output_dir, "voice_silk")], None
    return [], "本地微信媒体数据库中未找到语音数据"


def first_hex_id(blob: Any) -> str:
    if isinstance(blob, str):
        blob = blob.encode("utf-8", "ignore")
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return ""
    match = re.search(rb"([0-9a-fA-F]{32})", bytes(blob))
    return match.group(1).decode("ascii").lower() if match else ""


def export_video(db, row: dict[str, Any], asset_dir: Path, output_dir: Path):
    video_id = first_hex_id(row.get("packed_info_data") or row.get("packed_info"))
    base = Path(db.account_dir) / "msg" / "video"
    if base.is_dir():
        for source in base.rglob("*.mp4"):
            if not video_id or source.name.lower() == f"{video_id}.mp4":
                destination = asset_dir / f"{media_prefix(row)}.mp4"
                copied = copy_with_unique_name(source, destination)
                return [local_asset(copied, output_dir, "video", md5=video_id)], None
    return [], "本地微信缓存中未找到视频（可能未下载或已清理）"


def export_file(media, db, row: dict[str, Any], asset_dir: Path, output_dir: Path):
    media_row = {
        "server_id": row.get("server_id"),
        "content": row.get("message_content"),
        "packed_info": row.get("packed_info_data"),
    }
    try:
        original_name = media._file_name(media_row)
    except Exception:
        original_name = None
    if not original_name:
        return [], None  # Usually a web link/card rather than a file.

    base = Path(db.account_dir) / "msg" / "file"
    if base.is_dir():
        for source in base.rglob("*"):
            if source.is_file() and source.name == original_name:
                filename = safe_name(original_name, fallback="attachment", max_length=160)
                destination = asset_dir / f"{media_prefix(row)}_{filename}"
                copied = copy_with_unique_name(source, destination)
                return [
                    local_asset(
                        copied,
                        output_dir,
                        "file",
                        original_name=original_name,
                    )
                ], None
    return [], f"文件“{original_name}”不在本地缓存中（可能未下载或已清理）"


def export_local_media(
    db,
    media,
    row: dict[str, Any],
    chat_user: str,
    asset_dir: Path,
    output_dir: Path,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    base_type = base_message_type(row.get("local_type"))
    try:
        if base_type == 3:
            return export_image(media, row, chat_user, asset_dir, output_dir)
        if base_type == 34:
            return export_voice(db, row, chat_user, asset_dir, output_dir)
        if base_type == 43:
            return export_video(db, row, asset_dir, output_dir)
        if base_type == 49:
            return export_file(media, db, row, asset_dir, output_dir)
    except Exception as exc:
        return [], f"媒体恢复失败：{type(exc).__name__}: {exc}"
    return [], None


def recent_sessions(db, cutoff_timestamp: int, session_limit: int) -> list[dict[str, Any]]:
    """Read every visible session active since the cutoff."""

    for rel, path, _ in db._db_files:
        if os.path.basename(path) != "session.db":
            continue
        conn = db._open(rel)
        try:
            rows = conn.execute(
                "SELECT username, unread_count, summary, last_timestamp, "
                "last_msg_sender, last_sender_display_name "
                "FROM SessionTable WHERE is_hidden=0 AND last_timestamp>=? "
                "ORDER BY sort_timestamp ASC LIMIT ?",
                (cutoff_timestamp, session_limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "username": row["username"],
                "unread": row["unread_count"],
                "summary": row["summary"],
                "last_time": normalize_timestamp(row["last_timestamp"]),
                "last_sender": row["last_sender_display_name"]
                or row["last_msg_sender"],
            }
            for row in rows
            if row["username"]
        ]

    # Public API fallback for schema variants.
    return [
        session
        for session in db.get_sessions(limit=session_limit)
        if normalize_timestamp(session.get("last_time")) >= cutoff_timestamp
    ]


def recent_message_rows(
    db, chat_user: str, cutoff_timestamp: int, end_timestamp: int
) -> list[dict[str, Any]]:
    def query(tables):
        return db._shard_rows(
            tables,
            "WHERE create_time>=? AND create_time<=?",
            (cutoff_timestamp, end_timestamp),
            order_ext=db._MSG_ORDER_ASC,
        )

    rows = db._run_msg_query(chat_user, query) or []
    output: list[dict[str, Any]] = []
    for row in rows:
        output.append(
            {
                "local_id": row["local_id"],
                "local_type": row["local_type"],
                "real_sender_id": row["real_sender_id"],
                "create_time": normalize_timestamp(row["create_time"]),
                "message_content": row["message_content"],
                "source": row["source"],
                "packed_info_data": row["packed_info_data"],
                "compress_content": row["compress_content"],
                "server_id": row["server_id"],
                "sort_seq": row["sort_seq"],
            }
        )
    output.sort(
        key=lambda row: (
            row.get("create_time") or 0,
            row.get("sort_seq") or 0,
            row.get("local_id") or 0,
        )
    )
    return output


def resolve_sender(
    row: dict[str, Any],
    chat_user: str,
    chat_type: str,
    group_prefix_user: str,
    sender_index: dict[int, str],
    nickname_index: dict[str, str],
    self_info: dict[str, str],
) -> tuple[str, str]:
    sender_id = row.get("real_sender_id")
    if sender_id in (2, "2"):
        return "我", self_info.get("username") or ""

    sender_username = group_prefix_user
    if not sender_username:
        try:
            sender_username = sender_index.get(int(sender_id), "")
        except (TypeError, ValueError):
            sender_username = ""
    if not sender_username and chat_type == "contact":
        sender_username = chat_user

    message_type = base_message_type(row.get("local_type"))
    if message_type == 10000 and not sender_username:
        return "系统", ""
    display_name = nickname_index.get(sender_username, sender_username)
    if not display_name:
        display_name = "对方" if chat_type == "contact" else "未知成员"
    return display_name, sender_username


def serialize_message(
    db,
    media,
    row: dict[str, Any],
    chat_user: str,
    chat_type: str,
    asset_dir: Path,
    output_dir: Path,
    sender_index: dict[int, str],
    nickname_index: dict[str, str],
    self_info: dict[str, str],
    local_timezone,
    restore_media: bool,
) -> dict[str, Any]:
    type_code = int(row.get("local_type") or 0)
    message_type = str(db._msg_type_name(type_code))
    decoded = decode_message_content(db, row)
    group_prefix_user, decoded_without_prefix = (
        strip_group_prefix(decoded) if chat_type == "group" else ("", decoded)
    )
    content, details = human_summary(
        message_type, type_code, decoded_without_prefix
    )
    urls = extract_urls(decoded_without_prefix)
    assets = add_remote_assets(urls)

    if details.get("latitude") and details.get("longitude"):
        map_url = (
            "https://www.openstreetmap.org/?mlat="
            f"{details['latitude']}&mlon={details['longitude']}#map=16/"
            f"{details['latitude']}/{details['longitude']}"
        )
        if map_url not in urls:
            urls.append(map_url)
            assets.extend(add_remote_assets([map_url]))

    media_warning = None
    if restore_media and base_message_type(type_code) in MEDIA_BASE_TYPES:
        local_assets, media_warning = export_local_media(
            db, media, row, chat_user, asset_dir, output_dir
        )
        assets = local_assets + assets
        if local_assets and base_message_type(type_code) == 49:
            original_name = local_assets[0].get("original_name")
            if original_name:
                content = f"[文件] {original_name}"

    sender, sender_username = resolve_sender(
        row,
        chat_user,
        chat_type,
        group_prefix_user,
        sender_index,
        nickname_index,
        self_info,
    )
    timestamp = normalize_timestamp(row.get("create_time"))
    result: dict[str, Any] = {
        "time": iso_time(timestamp, local_timezone),
        "timestamp": timestamp,
        "sender": sender,
        "sender_username": sender_username or None,
        "type": message_type,
        "type_code": type_code,
        "content": content,
        "assets": assets,
        "message_id": {
            "server_id": row.get("server_id"),
            "sort_seq": row.get("sort_seq"),
            "local_id": row.get("local_id"),
        },
    }
    if details:
        result["details"] = details
    if media_warning:
        result["media_warning"] = media_warning
    return result


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def export_snapshot_chats(
    backend: MacOSSnapshotBackend,
    *,
    output_dir: Path,
    start: datetime,
    now: datetime,
    days: float,
    session_limit: int,
) -> int:
    """Write macOS snapshot chats using the same JSON contract as Windows."""

    chats = backend.recent_chats(
        int(start.timestamp()), int(now.timestamp()), session_limit
    )
    print(f"快照时间窗内候选活跃会话：{len(chats)}")
    used_stems: set[str] = set()
    exported_messages = 0
    for index, chat in enumerate(chats, start=1):
        chat_name = str(chat.get("chat_name") or chat.get("chat_username"))
        chat_user = str(chat.get("chat_username") or chat_name)
        stem = unique_chat_stem(chat_name, chat_user, used_stems)
        payload = backend.with_range(chat, start=start, end=now, days=days)
        json_path = output_dir / f"{stem}.json"
        write_json_atomic(json_path, payload)
        message_count = len(payload.get("messages", []))
        exported_messages += message_count
        print(f"[{index}/{len(chats)}] 快照已保存 {message_count} 条消息 -> {json_path}")

    print(
        f"导出完成：{len(chats)} 个会话，{exported_messages} 条消息，"
        f"输出目录：{output_dir}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="导出微信最近一周的活跃会话，每个会话保存为一个 JSON。"
    )
    parser.add_argument(
        "--platform",
        choices=("auto", "windows", "macos"),
        default="auto",
        help="数据来源平台；默认按当前系统选择。",
    )
    parser.add_argument(
        "--days",
        type=float,
        default=7.0,
        help="导出最近多少天，默认 7 天。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="输出目录，默认项目根目录下的 data。",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help="wechatauto 解密缓存目录，默认使用系统临时目录。",
    )
    parser.add_argument(
        "--account",
        default=None,
        help="多账号机器可显式指定账号目录名；默认自动匹配当前登录账号。",
    )
    parser.add_argument(
        "--db-dir",
        default=None,
        help="微信数据根目录；默认由 wechatauto 自动检测。",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="macOS 规范化快照文件或目录；与 --platform macos 一起使用。",
    )
    parser.add_argument(
        "--session-limit",
        type=int,
        default=10000,
        help="最多检查的最近会话数，默认 10000。",
    )
    parser.add_argument(
        "--chat",
        dest="selected_chats",
        action="append",
        default=[],
        metavar="ID_OR_NAME",
        help="只导出指定会话 ID 或显示名；可重复指定多个会话。",
    )
    parser.add_argument(
        "--skip-media",
        action="store_true",
        help="只导出消息和 URL，不复制/解密本地媒体。",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    if args.days <= 0:
        raise SystemExit("--days 必须大于 0")
    if args.session_limit <= 0:
        raise SystemExit("--session-limit 必须大于 0")

    output_dir = args.output.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone()
    start = now - timedelta(days=args.days)
    cutoff_timestamp = int(start.timestamp())
    end_timestamp = int(now.timestamp())
    print(
        f"导出时间窗：{start.isoformat(timespec='seconds')} 至 "
        f"{now.isoformat(timespec='seconds')}"
    )

    if args.snapshot is not None and args.platform == "windows":
        raise SystemExit("--snapshot 不能与 --platform windows 一起使用")
    selected_platform = args.platform
    if args.snapshot is not None and selected_platform == "auto":
        selected_platform = "macos"
    if selected_platform == "macos":
        backend = build_backend("macos", snapshot=args.snapshot)
        print(f"读取 macOS 规范化快照：{args.snapshot}")
        return export_snapshot_chats(
            backend,
            output_dir=output_dir,
            start=start,
            now=now,
            days=args.days,
            session_limit=args.session_limit,
        )

    backend = build_backend(
        selected_platform,
        db_dir=args.db_dir,
        account=args.account,
        workdir=workdir,
        output_dir=output_dir,
    )
    wechatauto = backend.wechatauto
    db = backend.db
    print(f"使用 wechatauto-replica {getattr(wechatauto, '__version__', 'unknown')}")
    print("正在初始化当前登录微信的本地数据库读取器……")
    self_info = db.get_self_info()
    print(
        "当前账号："
        f"{self_info.get('nick_name') or self_info.get('username') or db.wxid} "
        f"({self_info.get('username') or db.wxid})"
    )

    sessions = recent_sessions(db, cutoff_timestamp, args.session_limit)
    if args.selected_chats:
        selected = {str(value) for value in args.selected_chats}
        sessions = [
            session
            for session in sessions
            if str(session.get("username") or "") in selected
            or str(db.get_nickname(str(session.get("username") or "")) or "") in selected
        ]
        print(f"按 --chat 筛选后会话数：{len(sessions)}")
    print(f"时间窗内候选活跃会话：{len(sessions)}")
    if not sessions:
        print("没有找到时间窗内的活跃联系人或群聊。")
        return 0

    nickname_index = db._nickname_index()
    sender_index = db._sender_id_index()
    media = backend.media
    used_stems: set[str] = set()
    exported_chats = 0
    exported_messages = 0
    media_warnings = 0

    # SessionTable may theoretically contain duplicates; export each username once.
    seen_users: set[str] = set()
    for index, session in enumerate(sessions, start=1):
        chat_user = str(session.get("username") or "")
        if not chat_user or chat_user in seen_users:
            continue
        seen_users.add(chat_user)
        chat_name = db.get_nickname(chat_user) or chat_user
        chat_type = "group" if chat_user.endswith("@chatroom") else "contact"
        print(f"[{index}/{len(sessions)}] 读取：{chat_name}")

        rows = recent_message_rows(db, chat_user, cutoff_timestamp, end_timestamp)
        if not rows:
            # Explicitly satisfy the requirement to ignore conversations with no
            # actual messages in the selected window.
            continue

        stem = unique_chat_stem(chat_name, chat_user, used_stems)
        asset_dir = output_dir / "assets" / stem
        messages = []
        for row in rows:
            message = serialize_message(
                db=db,
                media=media,
                row=row,
                chat_user=chat_user,
                chat_type=chat_type,
                asset_dir=asset_dir,
                output_dir=output_dir,
                sender_index=sender_index,
                nickname_index=nickname_index,
                self_info=self_info,
                local_timezone=now.tzinfo,
                restore_media=not args.skip_media,
            )
            if message.get("media_warning"):
                media_warnings += 1
            messages.append(message)

        payload = {
            "schema_version": 1,
            "chat_name": chat_name,
            "chat_username": chat_user,
            "chat_type": chat_type,
            "account": {
                "username": self_info.get("username") or db.wxid,
                "nickname": self_info.get("nick_name") or "",
            },
            "range": {
                "start": start.isoformat(timespec="seconds"),
                "end": now.isoformat(timespec="seconds"),
                "days": args.days,
            },
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "message_count": len(messages),
            "messages": messages,
        }
        json_path = output_dir / f"{stem}.json"
        write_json_atomic(json_path, payload)
        exported_chats += 1
        exported_messages += len(messages)
        print(f"  已保存 {len(messages)} 条消息 -> {json_path}")

    print(
        f"导出完成：{exported_chats} 个会话，{exported_messages} 条消息，"
        f"输出目录：{output_dir}"
    )
    if media_warnings:
        print(
            f"提示：{media_warnings} 条媒体消息没有完整本地明文副本；"
            "JSON 中已保留可用 URL、缓存地址和 media_warning。"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        raise SystemExit(130)
    except BackendUnavailable as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(
            f"导出失败：{type(exc).__name__}: {exc}\n"
            "请确认平台参数、输入快照和 wechat-export 环境配置正确。",
            file=sys.stderr,
        )
        raise
