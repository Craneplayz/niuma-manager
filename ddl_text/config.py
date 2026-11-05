"""Runtime-neutral configuration; credentials remain outside request data."""

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Settings:
    mode: str = "offline"
    model: Optional[str] = None
    endpoint: Optional[str] = None
    credential_env: Optional[str] = None
    timeout_seconds: float = 5.0
    max_retries: int = 0
    thinking_mode: Optional[str] = None

    def __post_init__(self):
        if self.mode not in {"offline", "real"}:
            raise ValueError("Unsupported agent mode")
        if (isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, (int, float))
                or not math.isfinite(self.timeout_seconds)
                or not 0 < self.timeout_seconds <= 300):
            raise ValueError("timeout_seconds must be finite and within (0, 300]")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 2:
            raise ValueError("max_retries must be an integer from 0 to 2")
        if self.thinking_mode not in (None, 'enabled', 'disabled'):
            raise ValueError('thinking_mode must be enabled, disabled or null')
        for value in (self.model, self.endpoint, self.credential_env):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("Connection settings must be nonempty strings or null")
