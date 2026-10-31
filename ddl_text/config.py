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
    max_output_tokens: int = 8192
    truncation_retry_tokens: Optional[int] = None

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
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 131072:
            raise ValueError('max_output_tokens must be an integer within [1, 131072]')
        if self.truncation_retry_tokens is not None and (type(self.truncation_retry_tokens) is not int or
                not self.max_output_tokens < self.truncation_retry_tokens <= 131072):
            raise ValueError('truncation retry must exceed the initial output limit and be at most 131072')
        for value in (self.model, self.endpoint, self.credential_env):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("Connection settings must be nonempty strings or null")


EMAIL_RECOGNITION_TIMEOUT = 270

def email_model_settings(options):
    """Email execution settings shared by recognition, cache identity and replay."""
    disable_thinking = options['provider'] == 'qiniu' and options['model'].startswith('deepseek')
    return Settings(mode='real', model=options['model'], endpoint=options['endpoint'],
                    timeout_seconds=EMAIL_RECOGNITION_TIMEOUT, max_retries=0,
                    thinking_mode='disabled' if disable_thinking else None,
                    truncation_retry_tokens=16384 if options['provider']=='qiniu' else None)
