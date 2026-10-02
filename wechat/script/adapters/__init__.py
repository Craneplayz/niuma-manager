"""Platform adapters for the WeChat exporter.

The shared exporter owns the output contract; adapters only decide how a
platform supplies chat data.  Imports for Windows-only dependencies stay
inside the Windows adapter so macOS can run the snapshot pipeline without
loading COM or ``wechatauto``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .base import BackendUnavailable, detect_platform


def build_backend(
    platform_name: str,
    *,
    snapshot: Path | None = None,
    db_dir: str | None = None,
    account: str | None = None,
    workdir: Path | None = None,
    output_dir: Path | None = None,
) -> Any:
    """Create the requested backend without importing unrelated platform code."""

    selected = platform_name
    if selected == "auto":
        selected = detect_platform()

    if selected == "windows":
        from .windows import WindowsWechatBackend

        return WindowsWechatBackend.open(
            db_dir=db_dir,
            account=account,
            workdir=workdir,
            output_dir=output_dir,
        )
    if selected == "macos":
        if snapshot is None:
            raise BackendUnavailable(
                "macOS 目前通过 --snapshot 读取规范化快照；"
                "未提供快照文件或目录。"
            )
        from .macos import MacOSSnapshotBackend

        return MacOSSnapshotBackend(snapshot.expanduser().resolve())
    raise BackendUnavailable(
        f"当前平台 {sys.platform!r} 不支持实时微信读取；"
        "请在 Windows 使用 --platform windows，或提供 macOS 快照。"
    )


__all__ = ["BackendUnavailable", "build_backend", "detect_platform"]
