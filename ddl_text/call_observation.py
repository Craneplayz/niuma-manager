"""Optional, content-free observations from the model transport."""

import time
from . import diagnostics


def actual_usage(envelope):
    """Copy only provider-reported counts; missing/invalid is unknown, not zero."""
    usage = envelope.get('usage') if isinstance(envelope, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    result = {}
    for target, aliases in (
        ('input_tokens', ('prompt_tokens', 'input_tokens')),
        ('output_tokens', ('completion_tokens', 'output_tokens')),
        ('total_tokens', ('total_tokens',)),
    ):
        result[target] = next((usage[name] for name in aliases
                               if type(usage.get(name)) is int
                               and 0 <= usage[name] <= 9007199254740991), None)
    return result


def observe_call(observer, invoke):
    # Each call owns its metrics: never reuse usage from a preceding response.
    event = dict(contract_version='producer-observation/0.1', stage='model_call',
                 result_code='unavailable', **actual_usage(None))
    started = time.monotonic()
    diagnostics.emit('model_transport', status='started')
    try:
        return invoke(event)
    finally:
        event['duration_ms'] = max(0, round((time.monotonic() - started) * 1000, 3))
        diagnostics.emit('model_transport', status=event['result_code'], seconds=event['duration_ms']/1000,
                         **{k:event[k] for k in ('input_tokens','output_tokens','total_tokens')})
        if callable(observer):
            try:
                observer(dict(event))
            except Exception:
                pass  # Observability cannot turn a valid recognition into a failure.
