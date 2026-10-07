"""Build and submit WeChat v0.3.1 attachment requests.

The exporter remains deliberately offline by default.  Local paths are used
only while hashing/uploading an attachment and are never placed in a request.
Use ``--sync`` only after the user has supplied a server URL and an import
credential.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    from contracts.check_v031_examples import check_request as _shared_check_request
except ImportError:  # The exporter can run without the optional jsonschema dependency.
    _shared_check_request = None

try:
    from .extract_data import base_message_type, extract_urls, safe_name
except ImportError:  # Direct ``python wechat/script/v031.py`` execution.
    from extract_data import base_message_type, extract_urls, safe_name


CONTRACT_VERSION = "0.3.1"
MAX_REQUEST_BYTES = 2_000_000
MAX_RECORDS = 20
MAX_ATTACHMENTS = 10
MAX_CONTEXT = 100
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_QUEUE_ATTEMPTS = 5
SUPPORTED_MIME = {
    "image/jpeg",
    "image/png",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ALLOWED_QUERY_KEYS = {
    "id",
    "page",
    "view",
    "mode",
    "method",
    "context",
    "courseid",
    "handle",
    "contentid",
    "announcementid",
    "p",
    "lang",
}
FILE_REF_RE = re.compile(r"^asset:[A-Za-z0-9_-]{16,128}$")
REQUEST_ERROR_CODES = {
    "invalid_request",
    "unsupported_version",
    "limit_exceeded",
    "unauthorized",
    "forbidden",
    "not_found",
    "request_conflict",
    "queue_busy",
    "invalid_job_state",
    "asset_metadata_mismatch",
}
JOB_STATUSES = {"queued", "running", "succeeded", "partial_failed", "failed"}
RESULT_STATUSES = {"success", "no_tasks", "partial_success", "failed"}


class V031BuildError(ValueError):
    """Raised when an input cannot be represented without silent loss."""


class V031RemoteError(RuntimeError):
    """An HTTP error with the contract's stable error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status
        self.payload = dict(payload or {})
        super().__init__(f"{code}: {message}")

    def envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message[:1024],
                "retryable": self.retryable,
            }
        }


class AssetUploader(Protocol):
    def upload(self, path: Path, metadata: Mapping[str, Any]) -> Mapping[str, Any]:
        """Upload a local file and return a v0.3.1 asset acknowledgement."""


class V031Request(dict):
    """A JSON request with local-only recovery metadata kept off the wire."""

    def __init__(self, payload: Mapping[str, Any], *, recovery_assets: Mapping[str, Any] | None = None):
        super().__init__(payload)
        self._recovery_assets = dict(recovery_assets or {})


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest[:40]}"


def _bounded_identifier(value: Any, prefix: str, *fallback_parts: Any) -> str:
    candidate = _text(value).strip()
    if candidate and len(candidate) <= 256 and _clean_metadata(candidate):
        return candidate
    return _stable_id(prefix, *(fallback_parts or (candidate,)))


def _text(value: Any) -> str:
    return value if isinstance(value, str) else "" if value is None else str(value)


def _clean_metadata(value: str) -> bool:
    """Reject control, format and surrogate code points in contract metadata."""

    return not any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)


def _usable_identifier(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)) and value == 0:
        return False
    text = _text(value).strip()
    if not text:
        return False
    try:
        if int(text, 10) == 0:
            return False
    except ValueError:
        pass
    return True


def _request_base_id(value: Any) -> str:
    request_id = _text(value).strip()
    if not request_id or len(request_id) > 230 or not _clean_metadata(request_id):
        raise V031BuildError("request_id 必须是 1-230 个无控制字符的字符")
    return request_id


def _publication(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        precision = value.get("precision")
        if precision in {"datetime", "date", "unknown"}:
            if precision == "unknown":
                return {"value": None, "precision": "unknown", "basis": "unknown"}
            raw = value.get("value")
            basis = value.get("basis") if value.get("basis") in {"source", "upstream_unverified"} else "unknown"
            normalized = _publication(raw) if raw is not None else {
                "value": None,
                "precision": "unknown",
                "basis": "unknown",
            }
            if normalized["precision"] != "unknown":
                normalized["basis"] = basis
            return normalized
    raw = _text(value).strip()
    if not raw:
        return {"value": None, "precision": "unknown", "basis": "unknown"}
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None and parsed.tzinfo is not None:
        return {"value": raw, "precision": "datetime", "basis": "source"}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            dt.date.fromisoformat(raw)
        except ValueError:
            pass
        else:
            return {"value": raw, "precision": "date", "basis": "source"}
    return {"value": None, "precision": "unknown", "basis": "unknown"}


def _timezone(value: Any) -> tuple[str | None, str]:
    raw = _text(value).strip()
    if not raw:
        return "Asia/Shanghai", "caller_default"
    try:
        ZoneInfo(raw)
    except (KeyError, ValueError):
        return None, "unknown"
    return raw, "source"


def _collected_at(value: Any) -> str | None:
    raw = _text(value).strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return raw if parsed.tzinfo is not None else None


def _source_item_id(message: Mapping[str, Any]) -> str:
    message_id = message.get("message_id")
    if isinstance(message_id, Mapping):
        for key in ("server_id", "sort_seq", "local_id"):
            value = message_id.get(key)
            if _usable_identifier(value):
                return _bounded_identifier(value, "source", message)
    for key in ("server_id", "sort_seq", "local_id"):
        value = message.get(key)
        if _usable_identifier(value):
            return _bounded_identifier(value, "source", message)
    return _stable_id("message", _json_bytes(message))


def _file_name(asset: Mapping[str, Any], path: Path | None, fallback: str) -> str:
    candidate = _text(asset.get("original_name") or asset.get("file_name")).strip()
    if not candidate and path is not None:
        candidate = path.name
    candidate = candidate or fallback
    sanitized = safe_name(candidate, fallback=fallback, max_length=255)
    sanitized = "".join(
        "_" if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char
        for char in sanitized
    )
    return sanitized.rstrip(" .") or fallback


def _mime_type(asset: Mapping[str, Any], path: Path | None) -> str:
    explicit = _text(asset.get("mime_type")).strip().lower()
    if explicit:
        return explicit
    kind = _text(asset.get("kind")).lower()
    if "encrypted" in kind:
        if "image" in kind:
            # The cache is not readable yet, but it still represents an
            # image and must remain a decrypt_failed image attachment rather
            # than being misclassified as an unsupported binary type.
            return "image/jpeg"
        return "application/octet-stream"
    name = _text(asset.get("file_name") or asset.get("original_name"))
    suffix = path.suffix.lower() if path is not None else Path(name).suffix.lower()
    return MIME_BY_SUFFIX.get(suffix) or mimetypes.guess_type(str(path or ""))[0] or "application/octet-stream"


def _local_path(asset: Mapping[str, Any]) -> Path | None:
    raw = asset.get("path")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    raw = asset.get("local_path")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return None


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
            if size > MAX_FILE_BYTES:
                break
    return size, digest.hexdigest()


def _error(stage: str, code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {
        "stage": stage,
        "code": code,
        "message": message[:1024],
        "retryable": retryable,
    }


def _asset_ack(
    ack: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *,
    strict_file_name: bool = True,
) -> Mapping[str, Any]:
    if not isinstance(ack, Mapping):
        raise V031BuildError("上传回执不是 JSON 对象")
    if ack.get("contract_version") != CONTRACT_VERSION:
        raise V031BuildError("上传回执不是 v0.3.1")
    file_ref = _text(ack.get("file_ref"))
    if not FILE_REF_RE.fullmatch(file_ref):
        raise V031BuildError("上传回执缺少合法的 file_ref")
    keys = ("mime_type", "size_bytes", "sha256")
    if strict_file_name:
        keys = ("file_name",) + keys
    for key in keys:
        if ack.get(key) != metadata.get(key):
            raise V031BuildError(f"上传回执字段 {key} 与本地文件不一致")
    return ack


def _asset_error_attachment(
    *,
    asset_id: str,
    file_name: str,
    mime_type: str,
    size_bytes: int | None,
    sha256: str | None,
    origin: str,
    original_published_at: Mapping[str, Any],
    status: str,
    errors: list[Mapping[str, Any]],
    file_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "file_name": file_name,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "origin": origin if origin in {"original", "forwarded", "unknown"} else "unknown",
        "original_published_at": dict(original_published_at),
        "content_mode": "file",
        "file_ref": file_ref,
        "status": status,
        "errors": list(errors)[:20],
    }


def _attachment_from_asset(
    asset: Mapping[str, Any],
    *,
    chat_username: str,
    source_item_id: str,
    message: Mapping[str, Any],
    index: int,
    uploader: AssetUploader | None,
    upload_cache: dict[tuple[str, str, int], Mapping[str, Any]],
    recovery_assets: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    path = _local_path(asset)
    file_name = _file_name(asset, path, f"attachment-{index}")
    mime_type = _mime_type(asset, path)
    origin = _text(asset.get("origin")) or "unknown"
    original_time = _publication(asset.get("original_published_at") or message.get("original_published_at"))
    asset_seed = f"{chat_username}:{source_item_id}:{index}:{file_name}"
    asset_id = _stable_id("asset", asset_seed)
    if recovery_assets is not None and path is not None:
        recovery_assets[asset_id] = {"path": str(path)}
    kind = _text(asset.get("kind")).lower()
    # Encrypted WeChat caches are a read/decrypt failure, even when their
    # filename has an unsupported or unknown suffix.  Keep that distinction
    # so the caller can recover the cache instead of treating it as a format
    # that is permanently unsupported.
    if "encrypted" in kind:
        if path is None:
            return _asset_error_attachment(
                asset_id=asset_id,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=None,
                sha256=None,
                origin=origin,
                original_published_at=original_time,
                status="failed",
                errors=[_error("collect", "decrypt_failed", "微信媒体仍是加密缓存，未找到可读取的本机文件", True)],
            )
        try:
            if not path.is_file():
                raise FileNotFoundError(path)
            size_bytes, sha256 = _hash_file(path)
        except FileNotFoundError:
            return _asset_error_attachment(
                asset_id=asset_id,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=None,
                sha256=None,
                origin=origin,
                original_published_at=original_time,
                status="failed",
                errors=[_error("collect", "decrypt_failed", "微信媒体仍是加密缓存，未找到本机文件", True)],
            )
        except OSError as exc:
            return _asset_error_attachment(
                asset_id=asset_id,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=None,
                sha256=None,
                origin=origin,
                original_published_at=original_time,
                status="failed",
                errors=[_error("collect", "decrypt_failed", f"读取加密微信媒体失败：{type(exc).__name__}", True)],
            )
        return _asset_error_attachment(
            asset_id=asset_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            origin=origin,
            original_published_at=original_time,
            status="failed",
            errors=[_error("collect", "decrypt_failed", "微信媒体仍是加密缓存，未上传", True)],
        )
    if mime_type not in SUPPORTED_MIME:
        return _asset_error_attachment(
            asset_id=asset_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=None,
            sha256=None,
            origin=origin,
            original_published_at=original_time,
            status="unsupported",
            errors=[_error("collect", "unsupported_type", f"暂不支持 MIME 类型：{mime_type}", False)],
        )
    if path is None:
        return _asset_error_attachment(
            asset_id=asset_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=None,
            sha256=None,
            origin=origin,
            original_published_at=original_time,
            status="failed",
            errors=[_error("collect", "asset_missing", "附件没有本机文件路径", True)],
        )
    try:
        if not path.is_file():
            raise FileNotFoundError(path)
        size_bytes, sha256 = _hash_file(path)
    except FileNotFoundError:
        return _asset_error_attachment(
            asset_id=asset_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=None,
            sha256=None,
            origin=origin,
            original_published_at=original_time,
            status="failed",
            errors=[_error("collect", "asset_missing", f"未找到附件：{file_name}", True)],
        )
    except OSError as exc:
        return _asset_error_attachment(
            asset_id=asset_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=None,
            sha256=None,
            origin=origin,
            original_published_at=original_time,
            status="failed",
            errors=[_error("collect", "asset_missing", f"读取附件失败：{type(exc).__name__}", True)],
        )
    if size_bytes > MAX_FILE_BYTES:
        return _asset_error_attachment(
            asset_id=asset_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            origin=origin,
            original_published_at=original_time,
            status="failed",
            errors=[_error("collect", "limit_exceeded", "附件超过 20 MiB 限制", False)],
        )
    metadata = {
        "contract_version": CONTRACT_VERSION,
        "file_name": file_name,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "sha256": sha256,
    }
    if uploader is None:
        return _asset_error_attachment(
            asset_id=asset_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            origin=origin,
            original_published_at=original_time,
            status="failed",
            errors=[_error("upload", "upload_failed", "未启用附件上传；仅输出离线调试请求", True)],
        )
    cache_key = (sha256, mime_type, size_bytes)
    try:
        ack = upload_cache.get(cache_key)
        if ack is None:
            ack = _asset_ack(uploader.upload(path, metadata), metadata)
            upload_cache[cache_key] = ack
        else:
            # The cache is keyed by content identity.  A different display
            # name must not invalidate a valid receipt for the same bytes.
            ack = _asset_ack(ack, metadata, strict_file_name=False)
    except V031RemoteError as exc:
        return _asset_error_attachment(
            asset_id=asset_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            origin=origin,
            original_published_at=original_time,
            status="failed",
            errors=[_error("upload", "upload_failed", f"{exc.code}: {exc.message}", exc.retryable)],
        )
    except (V031BuildError, OSError) as exc:
        return _asset_error_attachment(
            asset_id=asset_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            origin=origin,
            original_published_at=original_time,
            status="failed",
            errors=[_error("upload", "upload_failed", str(exc), False)],
        )
    return _asset_error_attachment(
        asset_id=asset_id,
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        sha256=sha256,
        origin=origin,
        original_published_at=original_time,
        status="available",
        errors=[],
        file_ref=_text(ack["file_ref"]),
    )


def _context_record(context: Mapping[str, Any], chat: Mapping[str, Any]) -> dict[str, Any]:
    text = _text(context.get("text") or context.get("content"))
    context_id = _bounded_identifier(
        context.get("input_id"),
        "context",
        chat.get("chat_username"),
        context.get("message_id"),
        text,
    )
    published = _publication(context.get("published_at") or context.get("time"))
    timezone, _ = _timezone(context.get("timezone") or chat.get("timezone"))
    return {
        "input_id": context_id,
        "text": text,
        "published_at": published,
        "timezone": timezone,
        "platform": "wechat",
        "source_url": _safe_source_url(context.get("source_url")),
        "source_label": _text(context.get("source_label") or chat.get("chat_name")) or None,
    }


def _safe_source_url(value: Any) -> str | None:
    raw = _text(value).strip()
    if not raw:
        return None
    if not _clean_metadata(raw):
        return None
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        if parsed.scheme.lower() not in {"http", "https"} or not host:
            return None
        if parsed.username or parsed.password or parsed.fragment or any(ch.isspace() or ch == "\\" for ch in raw):
            return None
        allowed_port = 443 if parsed.scheme.lower() == "https" else 80
        if parsed.port not in (None, allowed_port):
            return None
        lowered_host = host.lower().rstrip(".")
        try:
            lowered_host = lowered_host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        if lowered_host.endswith((".localhost", ".local", ".localdomain", ".internal", ".intranet", ".lan", ".home", ".onion", ".invalid")):
            return None
        try:
            ip = ipaddress.ip_address(lowered_host)
        except ValueError:
            labels = lowered_host.split(".")
            if len(labels) < 2 or not re.fullmatch(r"[a-z][a-z0-9-]*", labels[-1]):
                return None
            if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in labels):
                return None
            ip = None
        if ip is not None and (not ip.is_global):
            return None
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=20)
        keys = [key.casefold().replace("_", "").replace("-", "") for key, _ in query]
        if len(keys) > 20 or len(set(keys)) != len(keys):
            return None
        if any(key not in ALLOWED_QUERY_KEYS for key in keys):
            return None
        if any(len(item) > 256 or any(ord(char) < 32 or ord(char) in (0x7F, 0x85) for char in item) for pair in query for item in pair):
            return None
        if re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", raw, re.I):
            return None
    except ValueError:
        return None
    return raw


def _message_urls(message: Mapping[str, Any], text: str) -> tuple[list[str], list[str]]:
    candidates = list(extract_urls(text))
    for asset in message.get("assets", []) if isinstance(message.get("assets"), list) else []:
        if isinstance(asset, Mapping) and _text(asset.get("url")):
            candidates.append(_text(asset["url"]))
    unique: list[str] = []
    for url in candidates:
        if url not in unique:
            unique.append(url)
    safe = [url for url in (_safe_source_url(value) for value in unique) if url]
    return safe, [url for url in unique if url not in safe]


def _asset_identity(asset: Mapping[str, Any]) -> tuple[str, str] | None:
    for key in ("md5", "sha256", "media_id", "media_key"):
        value = _text(asset.get(key)).strip().lower()
        if value:
            return key, value
    return None


def _coalesce_assets(raw_assets: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Drop an encrypted cache when its readable media sibling is present."""

    assets = list(raw_assets)
    readable_ids = {
        identity
        for asset in assets
        if "encrypted" not in _text(asset.get("kind")).lower()
        and _local_path(asset) is not None
        and _local_path(asset).is_file()
        for identity in [_asset_identity(asset)]
        if identity is not None
    }
    return [
        asset
        for asset in assets
        if not (
            "encrypted" in _text(asset.get("kind")).lower()
            and _asset_identity(asset) in readable_ids
        )
    ]


def _synthetic_asset(message: Mapping[str, Any]) -> Mapping[str, Any] | None:
    type_code = message.get("type_code")
    try:
        type_code = int(type_code)
    except (TypeError, ValueError):
        return None
    base_type = base_message_type(type_code)
    if base_type == 3:
        return {"kind": "image", "file_name": "image.jpg", "mime_type": "image/jpeg"}
    if base_type == 34:
        return {"kind": "voice", "file_name": "voice.silk", "mime_type": "audio/silk"}
    if base_type == 43:
        return {"kind": "video", "file_name": "video.mp4", "mime_type": "video/mp4"}
    if base_type == 49:
        content = _text(message.get("content"))
        details = message.get("details") if isinstance(message.get("details"), Mapping) else {}
        app_type = _text(details.get("app_message_type")).strip()
        # Type 49 also carries links/cards. Only the explicit WeChat file
        # variant may synthesize a file when no local asset was exported.
        if app_type and app_type != "6":
            return None
        if not content.startswith("[文件]"):
            return None
        name = content.removeprefix("[文件] ").strip() or "attachment.bin"
        return {"kind": "file", "file_name": name}
    return None


def _explicit_context(message: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]], chat: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_context = message.get("context")
    contexts: list[Mapping[str, Any]] = []
    if isinstance(raw_context, list):
        contexts.extend(item for item in raw_context if isinstance(item, Mapping))
    reply = message.get("reply_to")
    if isinstance(reply, Mapping):
        contexts.append(reply)
    elif reply not in (None, "") and str(reply) in by_id:
        contexts.append(by_id[str(reply)])
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in contexts:
        normalized = _context_record(item, chat)
        if normalized["input_id"] in seen:
            continue
        seen.add(normalized["input_id"])
        result.append(normalized)
    if len(result) > MAX_CONTEXT:
        raise V031BuildError("单条消息的明确上下文超过 100 条")
    return result


def message_to_record(
    message: Mapping[str, Any],
    chat: Mapping[str, Any],
    *,
    by_id: Mapping[str, Mapping[str, Any]] | None = None,
    uploader: AssetUploader | None = None,
    upload_cache: dict[tuple[str, str, int], Mapping[str, Any]] | None = None,
    recovery_assets: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    chat_username = _text(chat.get("chat_username") or chat.get("chat_name")) or "unknown-chat"
    source_item_id = _source_item_id(message)
    input_id = _bounded_identifier(
        message.get("input_id"),
        "input",
        chat_username,
        source_item_id,
    )
    if not message.get("input_id"):
        input_id = f"wechat:{_stable_id('chat', chat_username)[5:]}:{source_item_id}"
        if len(input_id) > 256:
            input_id = _stable_id("input", input_id)
    text = _text(message.get("content")).strip()
    raw_assets = message.get("assets") if isinstance(message.get("assets"), list) else []
    assets = _coalesce_assets(
        asset
        for asset in raw_assets
        if isinstance(asset, Mapping) and _text(asset.get("kind")).lower() != "url"
    )
    if not assets:
        synthetic = _synthetic_asset(message)
        if synthetic is not None:
            assets = [synthetic]
    cache = upload_cache if upload_cache is not None else {}
    attachments = [
        _attachment_from_asset(
            asset,
            chat_username=chat_username,
            source_item_id=source_item_id,
            message=message,
            index=index,
            uploader=uploader,
            upload_cache=cache,
            recovery_assets=recovery_assets,
        )
        for index, asset in enumerate(assets, start=1)
    ]
    if len(attachments) > MAX_ATTACHMENTS:
        raise V031BuildError(f"消息 {source_item_id} 的附件超过 10 个，未截断")
    if not text and not attachments:
        text = _text(message.get("summary")).strip()
    attachment_statuses = {attachment.get("status") for attachment in attachments}
    if attachments and (
        text in {"[图片]", "[语音]", "[视频]"}
        or (
            text.startswith("[文件]")
            and " — " not in text
            and attachment_statuses == {"available"}
        )
    ):
        text = ""
    text_quality = "complete" if text else "missing"
    if not text and not attachments:
        # A non-media system message still needs a lossless, non-empty body.
        text = "[微信消息无正文]"
        text_quality = "summary"
    if len(text) > 100000:
        raise V031BuildError(f"消息 {source_item_id} 正文超过 100,000 字符，未截断")
    timezone, timezone_basis = _timezone(message.get("timezone") or chat.get("timezone"))
    safe_urls, unsafe_urls = _message_urls(message, text)
    hints: dict[str, Any] | None = None
    if unsafe_urls:
        hints = {"unusable_links": unsafe_urls, "incomplete_content": True}
    if len(safe_urls) > 1:
        hints = hints or {}
        hints["unrepresented_links"] = safe_urls[1:]
        hints["incomplete_content"] = True
    warning = _text(message.get("media_warning"))
    if warning:
        hints = hints or {}
        hints["media_warning"] = warning
    contexts = _explicit_context(message, by_id or {}, chat)
    source_label = _text(chat.get("chat_name") or chat.get("chat_username")) or None
    if source_label is not None and (len(source_label) > 512 or not _clean_metadata(source_label)):
        raise V031BuildError(f"会话名称超过 512 字符，未截断：{source_item_id}")
    course = _text(message.get("course_or_project")).strip() or None
    if course is not None and (len(course) > 512 or not _clean_metadata(course)):
        raise V031BuildError(f"course_or_project 超过 512 字符，未截断：{source_item_id}")
    return {
        "input_id": input_id,
        "text": text,
        "text_quality": text_quality,
        "platform": "wechat",
        "published_at": _publication(message.get("time") or message.get("published_at")),
        "collected_at": _collected_at(chat.get("exported_at")),
        "timezone": timezone,
        "timezone_basis": timezone_basis,
        "source_url": safe_urls[0] if safe_urls else None,
        "source_label": source_label,
        "source_item_id": source_item_id,
        "course_or_project": course,
        "context": contexts,
        "upstream_hints": hints,
        "attachments": attachments,
    }


def _check_hint_limits(value: Any) -> None:
    if value is None:
        return
    if len(_json_bytes(value)) > 16_384:
        raise V031BuildError("upstream_hints 超过 16,384 字节限制")
    count = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 256:
            raise V031BuildError("upstream_hints 节点超过 256 个")
        if isinstance(item, (dict, list)):
            if depth > 4 or len(item) > 32:
                raise V031BuildError("upstream_hints 深度或宽度超过契约限制")
            if isinstance(item, dict):
                for key, nested in item.items():
                    if not isinstance(key, str) or len(key) > 64 or not _clean_metadata(key):
                        raise V031BuildError("upstream_hints 包含非法键")
                    stack.append((nested, depth + 1))
            else:
                stack.extend((nested, depth + 1) for nested in item)
        elif isinstance(item, str) and len(item) > 4096:
            raise V031BuildError("upstream_hints 文本超过 4,096 字符限制")


def _validate_request_contract(request: Mapping[str, Any]) -> None:
    try:
        if _shared_check_request is not None:
            _shared_check_request(request)
            return
        _check_request_size(request)
        records = request.get("records")
        if not isinstance(records, list):
            raise V031BuildError("请求 records 必须是数组")
        for record in records:
            if not isinstance(record, Mapping):
                raise V031BuildError("请求 record 必须是对象")
            _check_hint_limits(record.get("upstream_hints"))
            source_url = record.get("source_url")
            if source_url is not None and _safe_source_url(source_url) != source_url:
                raise V031BuildError("请求包含不安全 source_url")
    except V031BuildError:
        raise
    except Exception as exc:
        raise V031BuildError(f"请求不符合 v0.3.1 契约：{type(exc).__name__}: {exc}") from exc


def _check_request_size(request: Mapping[str, Any]) -> None:
    size = len(_json_bytes(request))
    if size > MAX_REQUEST_BYTES:
        raise V031BuildError(f"请求超过 2,000,000 字节限制（当前 {size}）")


def build_requests(
    chats: Iterable[Mapping[str, Any]],
    *,
    request_id: str | None = None,
    uploader: AssetUploader | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    upload_cache: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    recovery_assets: dict[str, dict[str, Any]] = {}
    seen_input_ids: set[str] = set()
    for chat in chats:
        if not isinstance(chat, Mapping):
            raise V031BuildError("输入会话必须是 JSON 对象")
        messages = chat.get("messages", [])
        if not isinstance(messages, list):
            raise V031BuildError("会话 messages 必须是数组")
        by_id: dict[str, Mapping[str, Any]] = {}
        for message in messages:
            if not isinstance(message, Mapping):
                raise V031BuildError("消息条目必须是 JSON 对象")
            source_item_id = _source_item_id(message)
            if source_item_id in by_id:
                raise V031BuildError(f"会话中 source_item_id 重复：{source_item_id}")
            by_id[source_item_id] = message
        for message in messages:
            record = message_to_record(
                message,
                chat,
                by_id=by_id,
                uploader=uploader,
                upload_cache=upload_cache,
                recovery_assets=recovery_assets,
            )
            if record["input_id"] in seen_input_ids:
                raise V031BuildError(f"请求内 input_id 重复：{record['input_id']}")
            seen_input_ids.add(record["input_id"])
            records.append(record)
    if not records:
        return []
    base_id = _request_base_id(request_id or f"wechat:{uuid.uuid4()}")
    requests: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    batch_index = 1

    def make_request(batch_records: list[dict[str, Any]], request_id_value: str) -> V031Request:
        payload = {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id_value,
            "records": batch_records,
        }
        request = V031Request(
            payload,
            recovery_assets={
                attachment["asset_id"]: recovery_assets[attachment["asset_id"]]
                for record in batch_records
                for attachment in record.get("attachments", [])
                if attachment.get("asset_id") in recovery_assets
            },
        )
        _check_request_size(request)
        _validate_request_contract(request)
        return request

    for record in records:
        candidate = batch + [record]
        candidate_request = {
            "contract_version": CONTRACT_VERSION,
            "request_id": f"{base_id}:{batch_index}",
            "records": candidate,
        }
        if batch and (len(candidate) > MAX_RECORDS or len(_json_bytes(candidate_request)) > MAX_REQUEST_BYTES):
            requests.append(make_request(batch, f"{base_id}:{batch_index}"))
            batch_index += 1
            batch = [record]
        else:
            batch = candidate
    if batch:
        requests.append(make_request(batch, f"{base_id}:{batch_index}"))
    return requests


def _multipart_body(path: Path, metadata: Mapping[str, Any]) -> tuple[bytes, str]:
    boundary = f"----niuma-wechat-{uuid.uuid4().hex}"
    filename = safe_name(_text(metadata.get("file_name")) or path.name, fallback="attachment", max_length=255)
    mime_type = _text(metadata.get("mime_type")) or "application/octet-stream"
    chunks: list[bytes] = []
    for key, value in metadata.items():
        if key == "contract_version":
            continue
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                f"{value}\r\n".encode(),
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {mime_type}\r\n\r\n".encode(),
            path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


@dataclass
class ClientSettings:
    server_url: str
    credential: str | None = None

    def set_server_url(self, value: str) -> None:
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("server_url 必须是 http(s) URL")
        self.server_url = value

    def clear_credential(self) -> None:
        self.credential = None


class WechatImportClient:
    """Small standard-library client for the v0.3.1 source-to-app endpoints."""

    def __init__(self, settings: ClientSettings, *, timeout: float = 30.0) -> None:
        settings.set_server_url(settings.server_url)
        self.settings = settings
        self.timeout = timeout

    def _request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.settings.credential:
            headers["Authorization"] = f"Bearer {self.settings.credential}"
        request = Request(self.settings.server_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_REQUEST_BYTES + 1)
                if len(raw) > MAX_REQUEST_BYTES:
                    raise V031RemoteError("limit_exceeded", "服务器响应超过 2,000,000 字节", retryable=False)
        except HTTPError as exc:
            raw = exc.read(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise V031RemoteError(
                    "limit_exceeded",
                    "服务器错误响应超过 2,000,000 字节",
                    retryable=False,
                    status=exc.code,
                ) from exc
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            error = payload.get("error") if isinstance(payload, Mapping) else None
            code = error.get("code") if isinstance(error, Mapping) else None
            message = error.get("message") if isinstance(error, Mapping) else None
            fallback_codes = {
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                409: "request_conflict",
                413: "limit_exceeded",
                503: "queue_busy",
            }
            fallback_code = fallback_codes.get(exc.code, "queue_busy" if exc.code >= 500 else "invalid_request")
            retryable = bool(error.get("retryable")) if isinstance(error, Mapping) and "retryable" in error else exc.code >= 500
            raise V031RemoteError(
                code if code in REQUEST_ERROR_CODES else fallback_code,
                _text(message) or f"服务器返回 HTTP {exc.code}",
                retryable=retryable,
                status=exc.code,
                payload=payload if isinstance(payload, Mapping) else {},
            ) from exc
        except (TimeoutError, URLError) as exc:
            raise V031RemoteError("queue_busy", f"服务器暂时不可达：{type(exc).__name__}", retryable=True) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise V031RemoteError("invalid_request", "服务器返回的 JSON 无法解析", retryable=False) from exc
        if not isinstance(payload, dict):
            raise V031RemoteError("invalid_request", "服务器返回的 JSON 不是对象", retryable=False)
        return payload

    def upload(self, path: Path, metadata: Mapping[str, Any]) -> Mapping[str, Any]:
        body, content_type = _multipart_body(path, metadata)
        return _asset_ack(self._request("POST", "/api/sources/wechat/assets", body, content_type), metadata)

    def submit_import(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        _validate_request_contract(request)
        body = _json_bytes(request)
        if len(body) > MAX_REQUEST_BYTES:
            raise V031RemoteError("limit_exceeded", "请求超过 2,000,000 字节限制", retryable=False)
        return validate_ack(self._request("POST", "/api/sources/wechat/imports", body, "application/json"), request_id=_text(request.get("request_id")))

    def get_job(self, job_id: str) -> Mapping[str, Any]:
        return validate_job(
            self._request("GET", f"/api/sources/wechat/imports/{quote(job_id, safe='')}"),
            job_id=job_id,
        )

    def retry_job(self, job_id: str, retry_id: str) -> Mapping[str, Any]:
        body = _json_bytes({"retry_id": retry_id})
        return validate_ack(self._request(
            "POST",
            f"/api/sources/wechat/imports/{quote(job_id, safe='')}/retry",
            body,
            "application/json",
        ), job_id=job_id)


def validate_ack(
    ack: Mapping[str, Any],
    *,
    request_id: str | None = None,
    job_id: str | None = None,
) -> Mapping[str, Any]:
    if not isinstance(ack, Mapping):
        raise V031BuildError("导入回执不是 JSON 对象")
    if ack.get("contract_version") != CONTRACT_VERSION or not _text(ack.get("job_id")) or not _text(ack.get("request_id")):
        raise V031BuildError("导入回执缺少 v0.3.1 job_id/request_id")
    if request_id is not None and _text(ack.get("request_id")) != request_id:
        raise V031BuildError("导入回执的 request_id 不匹配")
    if job_id is not None and _text(ack.get("job_id")) != job_id:
        raise V031BuildError("导入回执的 job_id 不匹配")
    if ack.get("status") not in JOB_STATUSES:
        raise V031BuildError("导入回执的 status 无效")
    return ack


def validate_result(
    result: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        raise V031BuildError("识别结果不是 JSON 对象")
    if result.get("contract_version") != CONTRACT_VERSION or result.get("status") not in RESULT_STATUSES:
        raise V031BuildError("识别结果不是合法的 v0.3.1 输出")
    if request_id is not None and _text(result.get("request_id")) != request_id:
        raise V031BuildError("识别结果的 request_id 不匹配")
    if not isinstance(result.get("record_results"), list) or not all(isinstance(item, Mapping) for item in result["record_results"]):
        raise V031BuildError("识别结果的 record_results 无效")
    if not isinstance(result.get("tasks"), list) or not all(isinstance(item, Mapping) for item in result["tasks"]):
        raise V031BuildError("识别结果的 tasks 无效")
    if not isinstance(result.get("errors"), list) or not all(isinstance(item, Mapping) for item in result["errors"]):
        raise V031BuildError("识别结果缺少 record_results/tasks/errors")
    if request is not None:
        expected_values = [record.get("input_id") for record in request.get("records", []) if isinstance(record, Mapping)]
        actual_values = [record.get("input_id") for record in result["record_results"] if isinstance(record, Mapping)]
        if len(expected_values) != len(set(expected_values)) or len(actual_values) != len(set(actual_values)) or set(expected_values) != set(actual_values):
            raise V031BuildError("识别结果没有为每条输入提供恰好一个 record_result")
    return result


def validate_job(
    job: Mapping[str, Any],
    *,
    job_id: str | None = None,
    request_id: str | None = None,
) -> Mapping[str, Any]:
    if not isinstance(job, Mapping):
        raise V031BuildError("job 回执不是 JSON 对象")
    if job.get("contract_version") != CONTRACT_VERSION:
        raise V031BuildError("job 回执不是 v0.3.1")
    if job_id is not None and _text(job.get("job_id")) != job_id:
        raise V031BuildError("job 回执的 job_id 不匹配")
    expected_request_id = request_id or _text(job.get("request_id"))
    if not expected_request_id or _text(job.get("request_id")) != expected_request_id:
        raise V031BuildError("job 回执缺少匹配的 request_id")
    status = job.get("status")
    progress = job.get("progress")
    if status not in JOB_STATUSES or not isinstance(progress, Mapping):
        raise V031BuildError("job 回执缺少合法 status/progress")
    total = progress.get("total_records")
    finished = progress.get("finished_records")
    failed = progress.get("failed_records")
    if not all(type(value) is int for value in (total, finished, failed)) or not (0 < total <= MAX_RECORDS and 0 <= failed <= finished <= total):
        raise V031BuildError("job 回执 progress 不一致")
    if status in {"queued", "running"} and job.get("result") is not None:
        raise V031BuildError("非终态 job 不得携带 result")
    if status in {"succeeded", "partial_failed", "failed"} and not isinstance(job.get("result"), Mapping):
        raise V031BuildError("终态 job 必须携带 result")
    if status in {"succeeded", "partial_failed", "failed"}:
        if finished != total:
            raise V031BuildError("终态 job 必须 finished_records 等于 total_records")
        result = validate_result(job["result"], request_id=expected_request_id)
        result_records = [item for item in result["record_results"] if isinstance(item, Mapping)]
        failed_records = sum(item.get("status") in {"failed", "partial_success"} for item in result_records)
        if failed_records != failed:
            raise V031BuildError("job failed_records 与 result 不一致")
        expected_status = {
            "succeeded": {"success", "no_tasks"},
            "partial_failed": {"partial_success"},
            "failed": {"failed"},
        }[status]
        if result.get("status") not in expected_status:
            raise V031BuildError("job status 与 result.status 不一致")
        if status == "succeeded" and failed != 0:
            raise V031BuildError("succeeded job 不得包含失败记录")
        if status == "partial_failed" and failed == 0:
            raise V031BuildError("partial_failed job 必须包含失败记录")
        if status == "failed" and failed != total:
            raise V031BuildError("failed job 必须所有记录失败")
    return job


class PendingQueue:
    """Persist requests/jobs and ignored local paths needed for asset recovery."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "batches": []}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("batches"), list):
            raise V031BuildError("待提交队列文件格式无效")
        for batch in payload["batches"]:
            if (
                not isinstance(batch, Mapping)
                or not isinstance(batch.get("request"), Mapping)
                or not _text(batch.get("request_id"))
                or _text(batch.get("request_id")) != _text(batch["request"].get("request_id"))
            ):
                raise V031BuildError("待提交队列包含无效批次")
            recovery = batch.get("recovery_assets", {})
            if not isinstance(recovery, Mapping) or any(
                not isinstance(value, Mapping) or not _text(value.get("path"))
                for value in recovery.values()
            ):
                raise V031BuildError("待提交队列包含无效附件恢复信息")
        return payload

    def _write(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def add(self, request: Mapping[str, Any]) -> None:
        if not isinstance(request, Mapping) or request.get("contract_version") != CONTRACT_VERSION:
            raise V031BuildError("只能将 v0.3.1 请求加入待提交队列")
        _validate_request_contract(request)
        request_id = _text(request.get("request_id"))
        if not request_id:
            raise V031BuildError("待提交请求缺少 request_id")
        payload = self._read()
        canonical = _json_bytes(request)
        for batch in payload["batches"]:
            if batch.get("request_id") == request_id:
                existing = batch.get("request")
                if _json_bytes(existing) != canonical:
                    raise V031BuildError(f"request_id 内容冲突：{request_id}")
                return
        recovery_assets = getattr(request, "_recovery_assets", {})
        if not isinstance(recovery_assets, Mapping):
            recovery_assets = {}
        payload["batches"].append(
            {
                "request_id": request_id,
                "request": request,
                "recovery_assets": {
                    str(asset_id): {"path": _text(value.get("path"))}
                    for asset_id, value in recovery_assets.items()
                    if isinstance(value, Mapping) and _text(value.get("path"))
                },
                "job_id": None,
                "attempts": 0,
                "last_error": None,
            }
        )
        self._write(payload)

    def pending(self) -> list[dict[str, Any]]:
        result = []
        for batch in self._read()["batches"]:
            if batch.get("job_id"):
                continue
            if int(batch.get("attempts") or 0) >= MAX_QUEUE_ATTEMPTS:
                continue
            last_error = batch.get("last_error")
            envelope = last_error.get("error") if isinstance(last_error, Mapping) else None
            if isinstance(envelope, Mapping) and envelope.get("retryable") is False:
                continue
            result.append(batch)
        return result

    def replace_request(self, request_id: str, request: Mapping[str, Any]) -> None:
        if request.get("request_id") != request_id:
            raise V031BuildError("替换队列请求时 request_id 不匹配")
        _validate_request_contract(request)
        payload = self._read()
        for batch in payload["batches"]:
            if batch.get("request_id") == request_id:
                recovery_assets = getattr(request, "_recovery_assets", {})
                batch["request"] = request
                batch["recovery_assets"] = {
                    str(asset_id): {"path": _text(value.get("path"))}
                    for asset_id, value in recovery_assets.items()
                    if isinstance(value, Mapping) and _text(value.get("path"))
                }
                self._write(payload)
                return
        raise V031BuildError(f"待提交队列中不存在 request_id：{request_id}")

    def supersede_request(self, request_id: str, request: Mapping[str, Any]) -> None:
        """Keep an attempted request immutable and enqueue repaired content under a new ID."""

        new_request_id = _text(request.get("request_id"))
        if not new_request_id or new_request_id == request_id:
            raise V031BuildError("恢复请求必须使用新的 request_id")
        _validate_request_contract(request)
        payload = self._read()
        for batch in payload["batches"]:
            if batch.get("request_id") == new_request_id:
                raise V031BuildError(f"恢复请求 request_id 已存在：{new_request_id}")
        found = False
        for batch in payload["batches"]:
            if batch.get("request_id") == request_id:
                found = True
                batch["job_id"] = f"superseded:{new_request_id}"
                batch["last_error"] = None
        if not found:
            raise V031BuildError(f"待提交队列中不存在 request_id：{request_id}")
        recovery_assets = getattr(request, "_recovery_assets", {})
        payload["batches"].append(
            {
                "request_id": new_request_id,
                "request": request,
                "recovery_assets": {
                    str(asset_id): {"path": _text(value.get("path"))}
                    for asset_id, value in recovery_assets.items()
                    if isinstance(value, Mapping) and _text(value.get("path"))
                },
                "job_id": None,
                "attempts": 0,
                "last_error": None,
            }
        )
        self._write(payload)

    def mark_submitted(self, request_id: str, job_id: str) -> None:
        payload = self._read()
        found = False
        for batch in payload["batches"]:
            if batch.get("request_id") == request_id:
                found = True
                batch["job_id"] = job_id
                batch["last_error"] = None
        if not found:
            raise V031BuildError(f"待提交队列中不存在 request_id：{request_id}")
        self._write(payload)

    def mark_error(self, request_id: str, error: Mapping[str, Any]) -> None:
        payload = self._read()
        found = False
        for batch in payload["batches"]:
            if batch.get("request_id") == request_id:
                found = True
                batch["attempts"] = int(batch.get("attempts") or 0) + 1
                batch["last_error"] = dict(error)
        if not found:
            raise V031BuildError(f"待提交队列中不存在 request_id：{request_id}")
        self._write(payload)


def _recover_failed_uploads(
    request: Mapping[str, Any],
    recovery_assets: Mapping[str, Any],
    uploader: AssetUploader,
) -> V031Request:
    """Re-upload retryable local failures before submitting the import."""

    recovered = copy.deepcopy(dict(request))
    changed = False
    for record in recovered.get("records", []):
        for attachment in record.get("attachments", []):
            if attachment.get("status") != "failed":
                continue
            errors = attachment.get("errors") if isinstance(attachment.get("errors"), list) else []
            upload_error = next(
                (error for error in errors if isinstance(error, Mapping) and error.get("code") == "upload_failed"),
                None,
            )
            if not isinstance(upload_error, Mapping) or not upload_error.get("retryable"):
                continue
            recovery = recovery_assets.get(attachment.get("asset_id")) if isinstance(recovery_assets, Mapping) else None
            path = Path(_text(recovery.get("path"))).expanduser() if isinstance(recovery, Mapping) else None
            if path is None or not path.is_file():
                raise V031RemoteError(
                    "asset_missing",
                    f"无法恢复附件本地文件：{attachment.get('file_name')}",
                    retryable=True,
                )
            metadata = {
                "contract_version": CONTRACT_VERSION,
                "file_name": attachment.get("file_name"),
                "mime_type": attachment.get("mime_type"),
                "size_bytes": attachment.get("size_bytes"),
                "sha256": attachment.get("sha256"),
            }
            try:
                ack = _asset_ack(uploader.upload(path, metadata), metadata)
            except V031RemoteError:
                raise
            except (V031BuildError, OSError) as exc:
                raise V031RemoteError("upload_failed", str(exc), retryable=True) from exc
            attachment.update({"status": "available", "file_ref": _text(ack["file_ref"]), "errors": []})
            changed = True
    return V031Request(recovered, recovery_assets=getattr(request, "_recovery_assets", recovery_assets) if changed else recovery_assets)


def _recovery_request(request: Mapping[str, Any]) -> V031Request:
    payload = copy.deepcopy(dict(request))
    payload["request_id"] = _stable_id("recovery", request.get("request_id"), payload.get("records"))
    return V031Request(payload, recovery_assets=getattr(request, "_recovery_assets", {}))


def submit_pending(client: WechatImportClient, queue: PendingQueue) -> list[Mapping[str, Any]]:
    responses: list[Mapping[str, Any]] = []
    for batch in queue.pending():
        request_id = _text(batch.get("request_id"))
        try:
            request = _recover_failed_uploads(
                batch["request"],
                batch.get("recovery_assets", {}),
                client,
            )
            if request.get("records") != batch["request"].get("records"):
                if batch.get("last_error") is not None:
                    recovered_request = _recovery_request(request)
                    queue.supersede_request(request_id, recovered_request)
                    request = recovered_request
                    request_id = _text(request["request_id"])
                else:
                    queue.replace_request(request_id, request)
            ack = client.submit_import(request)
            job_id = _text(ack.get("job_id"))
            if not job_id or _text(ack.get("request_id")) != request_id:
                raise V031BuildError("导入回执缺少匹配的 job_id/request_id")
            queue.mark_submitted(request_id, job_id)
            responses.append(ack)
        except (V031RemoteError, V031BuildError) as exc:
            error = exc.envelope() if isinstance(exc, V031RemoteError) else {"error": {"code": "invalid_request", "message": str(exc), "retryable": False}}
            queue.mark_error(request_id, error)
            responses.append(error)
    return responses


def _load_chats(paths: Iterable[Path]) -> list[Mapping[str, Any]]:
    chats: list[Mapping[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            if not all(isinstance(item, Mapping) for item in payload):
                raise V031BuildError(f"输入会话数组包含非对象条目：{path}")
            chats.extend(payload)
        elif isinstance(payload, Mapping):
            chats.append(payload)
        else:
            raise V031BuildError(f"输入不是会话 JSON 对象：{path}")
    return chats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将微信导出 JSON 转为 v0.3.1 请求；--sync 才会上传原件。")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=Path("wechat/data/requests-v031.jsonl"))
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--sync", action="store_true", help="授权后上传 JPG/PNG/PDF/DOCX 并提交 imports")
    parser.add_argument("--server-url", default=os.environ.get("WECHAT_IMPORT_URL"))
    parser.add_argument("--token-env", default="WECHAT_IMPORT_TOKEN")
    parser.add_argument("--queue-file", type=Path, default=Path("wechat/data/pending-v031.json"))
    parser.add_argument("--retry-pending", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.paths and not args.retry_pending:
            raise V031BuildError("至少提供一个会话 JSON；仅重试待提交队列时使用 --retry-pending")
        chats = _load_chats(args.paths) if args.paths else []
        client: WechatImportClient | None = None
        if args.sync or args.retry_pending:
            if not args.server_url:
                raise V031BuildError("--sync 需要 --server-url 或 WECHAT_IMPORT_URL")
            credential = os.environ.get(args.token_env)
            if not credential:
                raise V031BuildError(f"--sync 需要环境变量 {args.token_env} 中的限定导入凭据")
            client = WechatImportClient(ClientSettings(args.server_url, credential))
        requests = [] if args.retry_pending else build_requests(chats, request_id=args.request_id, uploader=client)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", newline="\n") as handle:
            for request in requests:
                handle.write(json.dumps(request, ensure_ascii=False) + "\n")
        if client is not None:
            queue = PendingQueue(args.queue_file)
            for request in requests:
                queue.add(request)
            responses = submit_pending(client, queue)
            for response in responses:
                print(json.dumps(response, ensure_ascii=False))
        else:
            print(f"已写出 {len(requests)} 个 v0.3.1 请求：{args.output}")
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"微信 v0.3.1 导出失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
