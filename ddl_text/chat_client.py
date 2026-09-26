"""Direct Chat Completions transport, bounded by runner's disposable process."""

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .adapters import AgentError
from .draft_schema import draft_schema


MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward a credential or private input to a redirected host.
        return None


class ChatClient:
    def __init__(self, key):
        self._key = key

    def generate(self, instructions, record_json, settings, attempt):
        schema = draft_schema(json.loads(record_json))
        return self.complete_json(instructions +
                                  "\nReturn only a JSON object matching this schema:\n" + json.dumps(schema),
                                  record_json, settings)

    def complete_json(self, instructions, input_json, settings):
        if not self._key.strip():
            raise AgentError("agent_unavailable", False)
        payload = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": input_json},
            ],
            "stream": False, "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        }
        request = Request(settings.endpoint.rstrip("/") + "/chat/completions",
                          data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": "Bearer " + self._key,
                                   "Content-Type": "application/json"})
        try:
            with build_opener(NoRedirect()).open(request, timeout=settings.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            # Fixed error classes only; never expose provider error bodies.
            retryable = error.code == 429 or 500 <= error.code < 600
            error.close()
            raise AgentError("agent_unavailable", retryable) from None
        except (TimeoutError, socket.timeout):
            raise AgentError("agent_timeout", True) from None
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise AgentError("agent_timeout", True) from None
            raise AgentError("agent_unavailable", False) from None
        except OSError:
            raise AgentError("agent_unavailable", False) from None
        try:
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            choices = json.loads(raw)["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("one complete choice required")
            choice = choices[0]
            message = choice["message"]
            if choice.get("finish_reason") != "stop" or message.get("tool_calls") or message.get("refusal"):
                raise ValueError("incomplete or unsupported answer")
            text = message["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty answer")
            # ExtractionAdapter validates the JSON draft and all source evidence.
            return text
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise AgentError("invalid_agent_output", False) from None
