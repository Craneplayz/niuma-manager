"""Direct Chat Completions transport, bounded by runner's disposable process."""

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .adapters import AgentError
from .draft_schema import draft_schema
from .model_usage import TOKEN_FIELDS, FINISH_REASONS, MAX_TOKEN_COUNT


MAX_RESPONSE_BYTES = 2 * 1024 * 1024



class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward a credential or private input to a redirected host.
        return None


def response_summary(raw):
    """Allowlisted structure only: never return model text or provider messages."""
    summary = {'response_bytes': len(raw), 'json_valid': False}
    try:
        envelope = json.loads(raw)
        summary['json_valid'] = True
        if not isinstance(envelope, dict):
            return summary
        choices = envelope.get('choices')
        summary['choice_count'] = len(choices) if isinstance(choices, list) else None
        if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
            choice = choices[0]
            reason = choice.get('finish_reason')
            summary['finish_reason'] = reason if reason in FINISH_REASONS else 'other'
            message = choice.get('message')
            if isinstance(message, dict):
                for name in ('content', 'reasoning_content'):
                    value = message.get(name)
                    summary[name + '_chars'] = len(value) if isinstance(value, str) else None
                summary['has_tool_calls'] = bool(message.get('tool_calls'))
                summary['has_refusal'] = bool(message.get('refusal'))
        usage = envelope.get('usage')
        if isinstance(usage, dict):
            for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                value = usage.get(name)
                if type(value) is int and 0 <= value <= MAX_TOKEN_COUNT:
                    summary[name] = value
            details = usage.get('completion_tokens_details')
            value = details.get('reasoning_tokens') if isinstance(details, dict) else None
            if type(value) is int and 0 <= value <= MAX_TOKEN_COUNT:
                summary['reasoning_tokens'] = value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        pass
    return summary


class ChatClient:
    def __init__(self, key, response_observer=None):
        self._key = key
        self._response_observer = response_observer
        self.response_metrics = {}

    def generate(self, instructions, record_json, settings, attempt):
        self.response_metrics = {}
        schema = draft_schema(json.loads(record_json))
        return self.complete_json(instructions +
                                  "\nReturn only a JSON object matching this schema:\n" + json.dumps(schema),
                                  record_json, settings)

    def complete_json(self, instructions, input_json, settings):
        self.response_metrics = {}
        if not self._key.strip():
            raise AgentError("agent_unavailable", False)
        payload = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": input_json},
            ],
            "stream": False, "max_tokens": settings.max_output_tokens,
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
            # Fixed error classes only; never expose provider error bodies.
            retryable = error.code == 429 or 500 <= error.code < 600
            if error.code == 429:
                self.response_metrics = {'provider_status': 429}
                try:
                    seconds = float(error.headers.get('Retry-After', ''))
                    if 0 <= seconds <= 300:
                        self.response_metrics['retry_after_seconds'] = seconds
                except (TypeError, ValueError, AttributeError):
                    pass
                if callable(self._response_observer):
                    try:
                        self._response_observer(dict(self.response_metrics))
                    except Exception:
                        pass
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
        self.response_metrics = response_summary(raw)
        if callable(self._response_observer):
            try:
                self._response_observer(dict(self.response_metrics))
            except Exception:
                pass  # Diagnostic observers cannot affect recognition.
        try:
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            choices = json.loads(raw)["choices"]
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
            return text
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise AgentError("invalid_agent_output", True, 'provider_response') from None
