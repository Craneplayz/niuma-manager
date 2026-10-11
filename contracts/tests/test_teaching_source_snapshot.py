"""Validate synthetic #107 source snapshots, without network/model access."""
import copy
import json
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from ddl_text.contract_v02 import validate_request

ROOT = Path(__file__).resolve().parents[1]

class TeachingSourceSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / 'schemas/teaching-source-snapshot-v0.2.schema.json').read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)
        cls.cases = json.loads((ROOT / 'source-to-text/teaching-source-snapshot-v02-cases.json').read_text(encoding="utf-8"))['cases']

    def test_emitted_requests_and_reading_metadata(self):
        for case in self.cases:
            with self.subTest(case=case['id']):
                validate_request(case['request'])
                record = case['request']['records'][0]
                self.assertEqual(record['text_quality'], case['expected_text_quality'])
                self.validator.validate(record['upstream_hints'])

    def test_partial_reference_cannot_claim_read(self):
        hints = copy.deepcopy(self.cases[1]['request']['records'][0]['upstream_hints'])
        hints['references'][0].update(read_status='partial', content_read=True)
        self.assertTrue(list(self.validator.iter_errors(hints)))

    def test_complete_reference_cannot_retain_failure_reason(self):
        case = next(case for case in self.cases if case['id'] == 'text-attachment-read')
        hints = copy.deepcopy(case['request']['records'][0]['upstream_hints'])
        hints['references'][0]['reason'] = 'download_failed'
        self.assertTrue(list(self.validator.iter_errors(hints)))

    def test_submission_state_is_independent_of_reading_quality_and_identity(self):
        cases = {case['id']: case['request']['records'][0] for case in self.cases}
        active = cases['assignment-active']
        submitted = cases['assignment-submitted-zh']
        other_course = cases['assignment-submitted-en-other-course']
        unknown = cases['assignment-unknown']
        self.assertEqual(submitted['upstream_hints']['assignment']['state'], 'submitted')
        self.assertEqual(submitted['text_quality'], 'partial')
        reference = submitted['upstream_hints']['references'][0]
        self.assertEqual(reference['read_status'], 'partial')
        self.assertFalse(reference['content_read'])
        self.assertEqual(reference['reason'], 'submission_history_only')
        self.assertEqual(active['input_id'], submitted['input_id'])
        self.assertEqual(active['source_item_id'], submitted['source_item_id'])
        self.assertNotEqual(other_course['input_id'], submitted['input_id'])
        self.assertEqual(other_course['source_item_id'], submitted['source_item_id'])
        self.assertEqual(unknown['upstream_hints']['assignment']['state'], 'unknown')
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(submitted))
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(other_course))

    def test_optional_provenance_requires_version_and_snapshot_but_cannot_grant_scope(self):
        hints = copy.deepcopy(self.cases[0]['request']['records'][0]['upstream_hints'])
        hints['provenance'] = dict(version='course-provenance-v1', course_id='synthetic',
            course_entry_url=None, source_metadata_text='', term_label=None,
            term_basis='unknown', applies_to='unknown')
        self.assertTrue(list(self.validator.iter_errors(hints)))
        hints['snapshot_id'] = 'pku:snapshot:' + 'a' * 64
        self.validator.validate(hints)
        for key, value in [('applies_to', 'current_assignment'), ('version', 'unversioned'), ('source_verified', True)]:
            bad = copy.deepcopy(hints)
            bad['provenance'][key] = value
            self.assertTrue(list(self.validator.iter_errors(bad)))
        hints['snapshot_id'] = 'client-trusted'
        self.assertTrue(list(self.validator.iter_errors(hints)))

    def test_teaching_v04_exports(self):
        from contracts.teaching_review_draft import validate_pair
        from contracts.check_v031_examples import check_source_url
        schema = json.loads((ROOT / 'schemas/teaching-request-v0.4.schema.json').read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for case in self.cases:
            with self.subTest(case=case['id']):
                export = case['teaching_export']
                self.assertFalse(export['skipped'])
                for request in export['requests']:
                    validator.validate(request)
                    for entry in request['records']:
                        check_source_url(entry['source']['url'])
                        record = entry['record']
                        assessment = dict(candidate_id='validation-only', input_id=record['input_id'],
                            action=dict(state='uncertain', evidence=[]),
                            deadline=dict(value=None, precision='unknown', derivation='unresolved', anchor=None, evidence=[]),
                            unread_relations=[dict(ref_id=ref['ref_id'], role='unknown', evidence=[])
                                              for ref in record['references'] if ref['read_state'] != 'complete'],
                            other_review_reasons=[])
                        validate_pair(dict(contract_version='0.4-draft.1', record=record, assessment=assessment))
                        self.assertIsNone(record['course_term'])
                        self.assertNotIn('source_verified', record)
        cases = {case['id']: case['teaching_export']['requests'][0]['records'][0]['record'] for case in self.cases}
        self.assertEqual(cases['supplementary-still-unread']['body_status'], 'complete')
        self.assertEqual(cases['supplementary-still-unread']['references'][0]['read_state'], 'unread')
        self.assertNotIn('附件正文：', cases['text-attachment-read']['text'])
        self.assertIn('请在2026年12月31日18点前提交报告。', cases['text-attachment-read']['references'][0]['text'])
        self.assertEqual(cases['assignment-unknown']['body_status'], 'partial')
        self.assertEqual(cases['day-publication']['published_at'], dict(value='2026-10-09', basis='source'))
        self.assertEqual(cases['empty-body']['text'], '')
        self.assertEqual(cases['empty-body']['body_status'], 'missing')
        self.assertIn('2026-10-23', cases['multiple-actions']['text'])
        self.assertIn('2026-10-25', cases['multiple-actions']['text'])
        self.assertIsNone(cases['missing-year-course-label']['course_term'])
