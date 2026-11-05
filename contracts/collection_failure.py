"""Safe optional crawler failure receipt; no arbitrary strings are admitted."""
import json

VERSION = 'collection-failure/0.1'
STAGES = ('credentials', 'browser', 'login', 'navigation', 'export', 'collection')
CODES = ('operation_failed', 'timeout', 'http_error', 'session_expired')
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['contract_version', 'stage', 'code'],
    'properties': {'contract_version': {'const': VERSION}, 'stage': {'enum': list(STAGES)},
                   'code': {'enum': list(CODES)}},
}


def parse_failure(stderr):
    """Old/foreign/oversized diagnostics remain unknown; never echo stderr."""
    if not isinstance(stderr, str) or len(stderr) > 16384:
        return None
    for line in reversed(stderr.splitlines()[-16:]):
        try:
            value = json.loads(line)
        except (ValueError, TypeError, RecursionError):
            continue
        if (isinstance(value, dict) and set(value) == set(SCHEMA['required'])
                and value.get('contract_version') == VERSION
                and value.get('stage') in STAGES and value.get('code') in CODES):
            return value
    return None
