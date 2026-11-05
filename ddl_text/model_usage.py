"""Content-free provider usage vocabulary shared across telemetry boundaries."""
TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens")
FINISH_REASONS = ("stop", "length", "tool_calls", "content_filter", "function_call", "other")
MAX_TOKEN_COUNT = 2**53 - 1
