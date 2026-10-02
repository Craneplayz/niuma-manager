"""Common adapter errors and platform detection."""

from __future__ import annotations

import sys


class BackendUnavailable(RuntimeError):
    """Raised when a platform data source cannot be used in this environment."""


def detect_platform() -> str:
    """Return the exporter platform name used by the command-line dispatcher."""

    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "unsupported"
