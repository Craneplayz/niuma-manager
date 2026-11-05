"""Diagnostics classify invalid synthetic output without retaining its content."""
import json
import unittest
from unittest.mock import patch
from ddl_text.adapters import AgentError
from ddl_text.extraction import compile_drafts, MAX_DRAFT_BYTES
from ddl_text.contracts import ContractError
from ddl_text.tests.test_extraction import message, draft

class ValidationDiagnosticsTests(unittest.TestCase):
    def assert_reason(self, response, record, reason):
        with self.assertRaises(AgentError) as caught: compile_drafts(response, record)
        self.assertEqual(caught.exception.code, 'invalid_agent_output')
        self.assertEqual(caught.exception.telemetry_reason, reason)
        self.assertNotIn('PRIVATE_SENTINEL', str(caught.exception))
    def test_json_schema_evidence_and_time_categories(self):
        record = message('PRIVATE_SENTINEL: 请交报告。')
        cases = [
            ('PRIVATE_SENTINEL', 'invalid_json'),
            ('{"tasks":NaN}', 'invalid_json'),
            ('{"tasks":[],"tasks":[]}', 'duplicate_keys'),
            ('x'*(MAX_DRAFT_BYTES+1), 'oversized_output'),
            ('{"tasks":"PRIVATE_SENTINEL"}', 'schema'),
            (json.dumps({'tasks':[draft(record,evidence=[{'input_id':'m','quote':'invented'}])]}), 'evidence'),
            (json.dumps({'tasks':[draft(record,times='PRIVATE_SENTINEL')]}), 'time'),
        ]
        for response, reason in cases:
            with self.subTest(reason=reason): self.assert_reason(response, record, reason)
    def test_candidate_validation_keeps_its_own_category(self):
        record=message()
        with patch('ddl_text.extraction.validate_tasks', side_effect=ContractError('invalid_agent_output')):
            self.assert_reason(json.dumps({'tasks':[draft(record)]}), record, 'candidate')
