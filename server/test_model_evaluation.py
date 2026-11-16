"""Offline regression for evaluation selection, resume and scoring boundaries."""
import contextlib
import copy
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.model_evaluation import runner
from server.model_evaluation.score_decomposition import score
from server.model_evaluation.summarize import summarize


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.fixtures = self.root / 'fixtures'
        shutil.copytree(runner.FIXTURES, self.fixtures)
        self.cases = runner.load_cases(self.fixtures / 'cases.json', self.fixtures / 'vision-cases.json')
        self.out = self.root / 'results'
        self.out.mkdir()

    def cli(self, *extra):
        argv = ['--cases', str(self.fixtures / 'cases.json'), '--vision-cases', str(self.fixtures / 'vision-cases.json'),
                '--model', 'test/model', '--source-version', 'fixture-version', '--out', str(self.out), *extra]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            runner.main(argv)
        return json.loads(output.getvalue())

    def test_fixed_dataset_and_dry_run_do_not_call_provider(self):
        self.assertEqual(len(self.cases), 28)
        self.assertEqual({m: sum(c['module'] == m for c in self.cases) for m in runner.MODULES},
                         dict(recognition=12, vision=4, decomposition=6, estimation=6))
        with patch.dict(os.environ, {}, clear=True), patch.object(runner, 'RecordedClient') as client:
            self.assertEqual(self.cli('--dry-run')['scheduled'], 28)
            client.assert_not_called()
        self.assertEqual(list(self.out.iterdir()), [])

    def test_module_and_single_case_selection(self):
        self.assertEqual(self.cli('--module', 'decomposition', '--dry-run')['cases'], 6)
        self.assertEqual(self.cli('--case-id', 'M1-roles', '--dry-run')['cases'], 1)

    def test_unknown_case_and_duplicate_models_are_rejected(self):
        for flags in [('--case-id', 'missing', '--dry-run'), ('--model', 'test/model', '--dry-run')]:
            with self.subTest(flags=flags), self.assertRaises(SystemExit):
                self.cli(*flags)

    def test_duplicate_case_is_rejected_before_selection(self):
        path = self.fixtures / 'cases.json'
        values = json.loads(path.read_text())
        path.write_text(json.dumps(values + [values[0]]))
        with self.assertRaises(ValueError):
            runner.load_cases(path)

    def test_image_bytes_and_endpoint_change_manifest(self):
        manifest = runner.build_manifest(self.cases, ['m'], self.fixtures, 'v', 'https://example.test/v1')
        image = self.fixtures / 'images/date6.png'
        image.write_bytes(image.read_bytes() + b'changed-fixture')
        changed = runner.build_manifest(self.cases, ['m'], self.fixtures, 'v', 'https://example.test/v1')
        self.assertNotEqual(manifest['run_id'], changed['run_id'])
        endpoint = runner.build_manifest(self.cases, ['m'], self.fixtures, 'v', 'https://other.test/v1')
        self.assertNotEqual(changed['run_id'], endpoint['run_id'])
        self.assertIn('runner.py', manifest['tool_hashes'])
        self.assertIn('M1-image-date6', manifest['image_hashes'])

    def test_image_escape_and_credential_endpoint_are_rejected(self):
        with self.assertRaises(ValueError):
            runner.image_path(dict(image='../private.png'), self.fixtures)
        with self.assertRaises(ValueError):
            runner.build_manifest(self.cases, ['m'], self.fixtures, 'v', 'https://user:secret@example.test')

    def test_completed_receipt_resumes_without_credentials(self):
        case = next(c for c in self.cases if c['id'] == 'M2-unknown')
        with patch.dict(os.environ, {'QINIU_API_KEY': 'synthetic'}), \
                patch.object(runner.decomposition, 'generate_decomposition', return_value={'status': 'needs_clarification'}):
            first = runner.run(case, 'm', self.out, self.fixtures, 'run', 'https://example.test')
        with patch.dict(os.environ, {}, clear=True), patch.object(runner.decomposition, 'generate_decomposition') as provider:
            self.assertEqual(runner.run(case, 'm', self.out, self.fixtures, 'run', 'https://example.test'), first)
            provider.assert_not_called()

    def test_invalid_cached_identity_is_not_silently_reused(self):
        case = self.cases[0]
        identity = runner.digest(dict(case=case, model='m', run_id='run'))
        runner.write_json(self.out / (identity + '.json'), {'id': 'other'})
        with self.assertRaises(ValueError):
            runner.run(case, 'm', self.out, self.fixtures, 'run', 'https://example.test')

    def test_incomplete_call_trace_does_not_duplicate_a_billed_call(self):
        case = self.cases[0]
        identity = runner.digest(dict(case=case, model='m', run_id='run'))
        (self.out / (identity + '.calls.jsonl')).write_text('{}\n')
        with self.assertRaises(ValueError):
            runner.run(case, 'm', self.out, self.fixtures, 'run', 'https://example.test')

    def test_provider_exception_text_is_not_serialized(self):
        case = next(c for c in self.cases if c['module'] == 'decomposition')
        with patch.dict(os.environ, {'QINIU_API_KEY': 'synthetic'}), \
                patch.object(runner.decomposition, 'generate_decomposition', side_effect=RuntimeError('secret-sentinel')):
            result = runner.run(case, 'm', self.out, self.fixtures, 'run', 'https://example.test')
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('secret-sentinel', json.dumps(result))

    def test_changed_manifest_cannot_mix_results(self):
        (self.out / 'manifest.json').write_text('{}')
        with self.assertRaises(SystemExit):
            self.cli('--case-id', 'M1-explicit')

    def score_one(self, case, output=None, status='ok'):
        evidence = self.root / 'evidence'
        evidence.mkdir(exist_ok=True)
        (evidence / 'cases.json').write_text(json.dumps([case]))
        (evidence / 'vision-cases.json').write_text('[]')
        receipt = dict(id=case['id'], module=case['module'], model='m', status=status,
                       output=output, seconds=0.01, observations=[], raw_outputs=[])
        (self.out / 'one.json').write_text(json.dumps(receipt))
        return summarize(evidence, self.out)

    def test_failed_negative_cannot_be_scored_as_correct(self):
        case = dict(id='negative', module='recognition', slice='negative', expected=dict(count=0, terms=[]))
        report = self.score_one(case, status='error')
        self.assertFalse(report['rows'][0]['metrics']['count_correct'])
        self.assertFalse(report['rows'][0]['metrics']['binary_correct'])

    def test_estimation_rejects_boolean_nonfinite_and_missing_ids(self):
        case = dict(id='estimate', module='estimation', expected={}, request=dict(tasks=[dict(id='task')]))
        for value in [True, 0, -1, math.inf, math.nan, '30']:
            with self.subTest(value=value):
                report = self.score_one(case, dict(estimates={'task': value}))
                self.assertFalse(report['rows'][0]['metrics']['positive_complete'])
        report = self.score_one(case, dict(estimates={}))
        self.assertFalse(report['rows'][0]['metrics']['positive_complete'])
        self.assertTrue(self.score_one(case, dict(estimates={'task': 30}))['rows'][0]['metrics']['positive_complete'])

    def test_missing_and_duplicate_receipts_are_rejected(self):
        case = dict(id='negative', module='recognition', slice='negative', expected=dict(count=0, terms=[]))
        self.score_one(case, [])
        evidence = self.root / 'evidence'
        (self.out / 'manifest.json').write_text(json.dumps(dict(case_ids=['negative'], models=['m', 'missing-model'])))
        with self.assertRaises(ValueError):
            summarize(evidence, self.out)
        (self.out / 'manifest.json').unlink()
        shutil.copy2(self.out / 'one.json', self.out / 'duplicate.json')
        with self.assertRaises(ValueError):
            summarize(evidence, self.out)

    def test_receipt_module_cannot_change_scoring_category(self):
        case = dict(id='negative', module='recognition', slice='negative', expected=dict(count=0, terms=[]))
        self.score_one(case, [])
        path = self.out / 'one.json'
        receipt = json.loads(path.read_text())
        receipt['module'] = 'estimation'
        path.write_text(json.dumps(receipt))
        with self.assertRaises(ValueError):
            summarize(self.root / 'evidence', self.out)

    def test_scoring_checks_recorded_run_and_case_fingerprint(self):
        case = dict(id='negative', module='recognition', slice='negative', expected=dict(count=0, terms=[]))
        self.score_one(case, [])
        path = self.out / 'one.json'
        receipt = json.loads(path.read_text())
        receipt.update(run_id='run', case_sha256=runner.digest(case))
        path.write_text(json.dumps(receipt))
        manifest = dict(case_ids=['negative'], models=['m'], run_id='run',
                        case_hashes={'negative': runner.digest(case)})
        (self.out / 'manifest.json').write_text(json.dumps(manifest))
        self.assertEqual(len(summarize(self.root / 'evidence', self.out)['rows']), 1)
        receipt['run_id'] = 'other-run'
        path.write_text(json.dumps(receipt))
        with self.assertRaises(ValueError):
            summarize(self.root / 'evidence', self.out)
        receipt['run_id'] = 'run'
        path.write_text(json.dumps(receipt))
        case['expected']['count'] = 1
        (self.root / 'evidence/cases.json').write_text(json.dumps([case]))
        with self.assertRaises(ValueError):
            summarize(self.root / 'evidence', self.out)

    def test_failed_estimation_does_not_pass_explicit_constraint(self):
        case = dict(id='estimate', module='estimation', expected=dict(minutes=30),
                    request=dict(tasks=[dict(id='task')]))
        report = self.score_one(case, dict(estimates={'task': 30}), status='error')
        self.assertFalse(report['rows'][0]['metrics']['explicit_correct'])

    def test_structured_missing_information_is_not_a_plan(self):
        case = dict(id='decomp', module='decomposition', slice='unknown', expected=dict(status='needs_clarification'))
        report = self.score_one(case, dict(status='needs_clarification', plan=None))
        self.assertTrue(report['rows'][0]['metrics']['structured_missing_info'])
        self.assertFalse(report['rows'][0]['metrics']['usable_plan'])

    def test_invalid_draft_is_safe_and_known_actions_are_not_generic_truth(self):
        case = dict(id='partial', module='decomposition', request=dict(availability={}))
        row = dict(id='partial', module='decomposition', model='m', status='error', raw_outputs=['[]'])
        rubric = dict(cases={'partial': [['格式'], ['上传']]})
        self.assertEqual(score(dict(rows=[row]), rubric, [case])['rows'][0]['raw_covered'], 0)
        draft = dict(plan=dict(subtasks=[dict(title='检查格式并上传', outcome='提交完成', scheduled_start='2026-11-20T10:00:00+08:00')]))
        row['raw_outputs'] = [json.dumps(draft)]
        result = score(dict(rows=[row]), rubric, [case])['rows'][0]
        self.assertEqual((result['raw_covered'], result['effective_covered'], result['unsupported_slots']), (2, 0, 1))
        case['request']['availability'] = dict(available_windows=[{'start': 'provided'}])
        self.assertEqual(score(dict(rows=[row]), rubric, [case])['rows'][0]['unsupported_slots'], 0)


if __name__ == '__main__':
    unittest.main()
