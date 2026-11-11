"""Synthetic HTTP transport responses; no live service or private materials."""
import json
import socket
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from jsonschema import Draft202012Validator

from ddl_text.adapters import AgentError
from ddl_text.chat_client import ChatClient
from ddl_text.config import Settings

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / 'contracts/schemas/producer-observation-v0.1.schema.json').read_text())
SECRET = 'PRIVATE_KEY_ACCOUNT_URL_BODY'


class CallObservationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.client = ChatClient(SECRET, call_observer=self.events.append)
        self.settings = Settings(mode='real', model='synthetic', endpoint='https://example.invalid')

    def call(self, envelope=None, error=None, observer=None):
        if observer is not None:
            self.client = ChatClient(SECRET, call_observer=observer)
        with patch('ddl_text.chat_client.build_opener') as opener, \
                patch('ddl_text.call_observation.time.monotonic', side_effect=[10, 10.125]):
            if error is not None:
                opener.return_value.open.side_effect = error
            else:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
            try:
                return self.client.complete_json(SECRET, SECRET, self.settings)
            finally:
                self.assertEqual(opener.return_value.open.call_count, 1)

    def response(self, usage=None, finish='stop'):
        return {'choices': [{'finish_reason': finish, 'message': {'content': '{"ok":true}', 'reasoning_content': SECRET}}],
                'usage': usage, 'private': SECRET}

    def test_provider_counts_and_unknown_values_round_trip_contract(self):
        for usage, expected in [
            ({'prompt_tokens': 12, 'completion_tokens': 4, 'total_tokens': 999, 'secret': SECRET}, (12, 4, 999)),
            ({'input_tokens': 0, 'output_tokens': 0}, (0, 0, None)),
            (None, (None, None, None)),
            ({'prompt_tokens': True, 'completion_tokens': -1, 'total_tokens': '100'}, (None, None, None)),
            ({'prompt_tokens': 9007199254740992, 'completion_tokens': 1.5}, (None, None, None)),
        ]:
            self.assertEqual(self.call(self.response(usage)), '{"ok":true}')
            event = self.events[-1]
            Draft202012Validator(SCHEMA).validate(event)
            self.assertEqual(tuple(event[f] for f in ('input_tokens', 'output_tokens', 'total_tokens')), expected)
            self.assertEqual(event['duration_ms'], 125)
            self.assertEqual(event['result_code'], 'ok')
            self.assertNotIn(SECRET, json.dumps(event))

    def test_failures_are_fixed_codes_and_never_reuse_prior_usage(self):
        self.call(self.response({'prompt_tokens': 10}))
        for failure, code in [
            (HTTPError('https://' + SECRET, 429, SECRET, {}, BytesIO(SECRET.encode())), 'rate_limited'),
            (HTTPError('https://' + SECRET, 500, SECRET, {}, BytesIO(SECRET.encode())), 'http_error'),
            (URLError(socket.timeout(SECRET)), 'timeout'),
            (TimeoutError(SECRET), 'timeout'),
            (OSError(SECRET), 'unavailable'),
        ]:
            with self.assertRaises(AgentError):
                self.call(error=failure)
            event = self.events[-1]
            Draft202012Validator(SCHEMA).validate(event)
            self.assertEqual(event['result_code'], code)
            self.assertIsNone(event['input_tokens'])
            self.assertNotIn(SECRET, json.dumps(event))
        with self.assertRaises(AgentError) as caught:
            self.call(self.response({'prompt_tokens': 10}, finish='length'))
        self.assertEqual(caught.exception.diagnostic, 'output_truncated')
        self.assertEqual(self.events[-1]['result_code'], 'invalid_response')
        self.assertEqual(self.events[-1]['input_tokens'], 10)

    def test_optional_observer_does_not_change_return_or_error(self):
        broken = Mock(side_effect=RuntimeError(SECRET))
        self.assertEqual(self.call(self.response(), observer=broken), '{"ok":true}')
        with self.assertRaises(AgentError) as caught:
            self.call(error=TimeoutError(), observer=broken)
        self.assertEqual(caught.exception.code, 'agent_timeout')
        self.client = ChatClient(SECRET)
        self.assertEqual(self.call(self.response()), '{"ok":true}')

    def test_shared_model_examples_match_actual_wire_shape(self):
        examples = json.loads((ROOT / 'contracts/examples/producer-observations-v01.json').read_text())
        for event in examples:
            if event['stage'] == 'model_call':
                Draft202012Validator(SCHEMA).validate(event)
