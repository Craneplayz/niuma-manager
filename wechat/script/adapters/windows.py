"""Windows live WeChat adapter.

All imports here are lazy because ``comtypes`` and ``wechatauto`` are Windows
dependencies.  The rest of the exporter can therefore be tested on macOS.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import BackendUnavailable


def prepare_comtypes_cache(project_root: Path) -> None:
    """Keep generated COM modules in the writable project cache."""

    cache_dir = project_root / ".cache" / "comtypes" / "gen"
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        import comtypes  # type: ignore
    except ImportError as exc:
        raise BackendUnavailable(
            "Windows 导出需要 comtypes，请在 wechat-export 环境安装 wechatauto-replica。"
        ) from exc

    current = sys.modules.get("comtypes.gen")
    if current is None:
        generated = types.ModuleType("comtypes.gen")
        generated.__package__ = "comtypes"
        generated.__path__ = [str(cache_dir)]  # type: ignore[attr-defined]
        sys.modules["comtypes.gen"] = generated
        setattr(comtypes, "gen", generated)
        return

    paths = list(getattr(current, "__path__", []))
    if str(cache_dir) not in paths:
        paths.insert(0, str(cache_dir))
        current.__path__ = paths  # type: ignore[attr-defined]


def import_wechat_packages(project_root: Path):
    prepare_comtypes_cache(project_root)
    try:
        import wechatauto  # type: ignore
        from wechatauto import MediaDownloader, WeChatDB  # type: ignore
    except ImportError as exc:
        raise BackendUnavailable(
            "未找到 wechatauto。请使用 wechat-export 环境运行：\n"
            "  conda run -n wechat-export python wechat/script/extract_data.py"
        ) from exc
    return wechatauto, WeChatDB, MediaDownloader


@dataclass
class WindowsWechatBackend:
    """Live database and media readers supplied by wechatauto-replica."""

    wechatauto: Any
    db: Any
    media: Any

    @classmethod
    def open(
        cls,
        *,
        db_dir: str | None,
        account: str | None,
        workdir: Path | None,
        output_dir: Path | None,
    ) -> "WindowsWechatBackend":
        project_root = Path(__file__).resolve().parents[2]
        wechatauto, WeChatDB, MediaDownloader = import_wechat_packages(project_root)
        db = WeChatDB(
            db_dir=db_dir,
            account=account,
            workdir=str(workdir) if workdir is not None else None,
        )
        media = MediaDownloader(db, save_dir=str(output_dir / "assets"))
        return cls(wechatauto=wechatauto, db=db, media=media)
