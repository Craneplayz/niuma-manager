"""Direct Chat Completions transport, bounded by runner's disposable process."""

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .adapters import AgentError
from .draft_schema import draft_schema
from .call_observation import actual_usage, observe_call
from . import diagnostics


MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward a credential or private input to a redirected host.
        return None


class ChatClient:
    def __init__(self, key, *, call_observer=None):
        self._key = key
        self._call_observer = call_observer

    def generate(self, instructions, record_json, settings, attempt):
        schema = draft_schema(json.loads(record_json))
        return self.complete_json(instructions +
                                  "\nReturn only a JSON object matching this schema:\n" + json.dumps(schema),
                                  record_json, settings)

    def complete_json(self, instructions, input_json, settings):
        return observe_call(self._call_observer, lambda event:
                            self._complete_json(instructions, input_json, settings, event))

    def _complete_json(self, instructions, input_json, settings, event):
        if not self._key.strip():
            raise AgentError("agent_unavailable", False, 'model_configuration')
        payload = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": input_json},
            ],
            "stream": False, "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        }
        if settings.thinking_mode is not None:
            payload['thinking'] = {'type': settings.thinking_mode}
        request = Request(settings.endpoint.rstrip("/") + "/chat/completions",
                          data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": "Bearer " + self._key,
                                   "Content-Type": "application/json"})
        try:
            with build_opener(NoRedirect()).open(request, timeout=settings.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            diagnostics.emit('provider_http', http_status=error.code)
            # Fixed error classes only; never expose provider error bodies.
            retryable = error.code == 429 or 500 <= error.code < 600
            event['result_code'] = 'rate_limited' if error.code == 429 else 'http_error'
            error.close()
            raise AgentError("agent_unavailable", retryable,
                'model_rate_limited' if error.code == 429 else 'model_configuration' if error.code in (401,403) else 'model_unavailable') from None
        except (TimeoutError, socket.timeout):
            event['result_code'] = 'timeout'
            raise AgentError("agent_timeout", True) from None
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                event['result_code'] = 'timeout'
                raise AgentError("agent_timeout", True) from None
            raise AgentError("agent_unavailable", False) from None
        except OSError:
            raise AgentError("agent_unavailable", False) from None
        try:
            event['result_code'] = 'invalid_response'
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            envelope = json.loads(raw)
            event.update(actual_usage(envelope))
            choices = envelope["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("one complete choice required")
            choice = choices[0]
            if choice.get('finish_reason') == 'length':
                raise AgentError('invalid_agent_output', True, 'output_truncated')
            message = choice["message"]
            if choice.get("finish_reason") != "stop" or message.get("tool_calls") or message.get("refusal"):
                raise ValueError("incomplete or unsupported answer")
            text = message["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty answer")
            # ExtractionAdapter validates the JSON draft and all source evidence.
            event['result_code'] = 'ok'
            return text
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise AgentError("invalid_agent_output", True, 'provider_response') from None
