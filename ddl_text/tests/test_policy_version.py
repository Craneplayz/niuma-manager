import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ddl_text import policy_version as policy
from ddl_text.config import Settings
from ddl_text.contract_v02 import validate_request, validate_result
from ddl_text.tests.reprocessing_cases import cases, replay


class PolicyVersionTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode='real', model='synthetic-model', endpoint='https://example.invalid/v1', timeout_seconds=45)

    def manifest(self, **kwargs):
        return policy.text_policy_manifest(kwargs.pop('settings', self.settings), provider=kwargs.pop('provider', 'qiniu'), **kwargs)

    def test_repeatable_and_credential_rotation_does_not_change_version(self):
        first = self.manifest()
        with patch.dict(os.environ, {'QINIU_API_KEY': 'PRIVATE-KEY-SENTINEL', 'TEXT_API_KEY': 'ANOTHER-SECRET'}):
            second = self.manifest(settings=replace(self.settings, credential_env='OTHER_KEY_NAME'))
        self.assertEqual(first, second)
        self.assertNotIn('PRIVATE', json.dumps(second))
        self.assertEqual(set(first['components']), set(policy.COMPONENTS))
        self.assertRegex(first['version'], r'^text-policy-v1:[0-9a-f]{64}$')

    def test_rule_code_changes_but_checkout_path_newlines_docs_do_not(self):
        initial = self.manifest()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in policy.COMPONENTS:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                original = (policy.ROOT / name).read_text(encoding='utf-8')
                path.write_bytes(b'\xef\xbb\xbf' + original.replace('\n', '\r\n').encode('utf-8'))
            (root / 'README.md').write_text('unrelated documentation')
            (root / '.env').write_text('PRIVATE-KEY-SENTINEL')
            with patch.object(policy, 'ROOT', root):
                self.assertEqual(initial, self.manifest())
                for name in ('skills/text-task-extraction/SKILL.md', 'classification.py', 'dates.py', 'chat_client.py'):
                    path = root / name
                    before = path.read_bytes()
                    path.write_bytes(before + b'\n# changed rule\n')
                    self.assertNotEqual(initial['version'], self.manifest()['version'])
                    path.write_bytes(before)
                (root / 'classification.py').unlink()
                with self.assertRaises(FileNotFoundError):
                    self.manifest()

    def test_actual_model_provider_contract_and_limits_are_identified(self):
        initial = self.manifest()['version']
        for kwargs in (dict(settings=replace(self.settings, model='different-model')),
                       dict(provider='codex'), dict(contract_version='0.1'), dict(deployment_revision='model-revision-2'),
                       dict(settings=replace(self.settings, endpoint='https://another.invalid/v1')),
                       dict(settings=replace(self.settings, timeout_seconds=60)),
                       dict(settings=replace(self.settings, max_retries=1))):
            self.assertNotEqual(initial, self.manifest(**kwargs)['version'])
        self.assertEqual(initial, self.manifest(settings=replace(self.settings, endpoint='https://EXAMPLE.invalid:443/v1/'))['version'])

    def test_unsupported_and_secret_bearing_configuration_rejected(self):
        for url in ('https://u:secret@example.invalid/v1', 'https://example.invalid/v1?key=secret',
                    'https://example.invalid/v1#secret', 'file:///tmp/model', 'https://bad\nhost/v1'):
            with self.assertRaises(ValueError):
                self.manifest(settings=replace(self.settings, endpoint=url))
        for kwargs in (dict(settings=Settings()), dict(provider='custom'), dict(contract_version='0.3.1'),
                       dict(settings=replace(self.settings, model=None)), dict(settings=replace(self.settings, endpoint=None))):
            with self.assertRaises(ValueError):
                self.manifest(**kwargs)


class ReprocessingHandoffTests(unittest.TestCase):
    def test_synthetic_handoff_has_valid_before_and_after_results(self):
        for case in cases():
            with self.subTest(name=case['name']):
                req = case['request']
                validate_request(req)
                before, after = replay(case, 'before'), replay(case, 'after')
                validate_result(req, before); validate_result(req, after)
                self.assertNotIn('policy_version', before)
                if case['name'] == 'new-recognition-fails':
                    self.assertEqual(after['status'], 'failed')
                    self.assertEqual(before['status'], 'success')
                elif case['name'] == 'multiple-actions-reordered':
                    self.assertEqual(len(after['tasks']), 2)
                    self.assertEqual(before['tasks'][0]['candidate_id'], after['tasks'][0]['candidate_id'])
                    self.assertNotEqual(before['tasks'][0]['deadline']['value'], after['tasks'][0]['deadline']['value'])
                else:
                    self.assertEqual(after['status'], 'success')
                    self.assertFalse(after['tasks'][0]['needs_review'])
                    self.assertEqual(after['tasks'][0]['deadline']['value'], '2026-10-08T18:00:00+08:00')
                    if case['name'] == 'no-tasks-to-task':
                        self.assertEqual(before['status'], 'no_tasks')
                    else:
                        self.assertTrue(before['tasks'][0]['needs_review'])

    def test_account_and_user_decision_matrix_is_explicit_not_runtime_claim(self):
        data = cases()
        self.assertEqual({c['user_decision'] for c in data}, {'pending', 'confirmed', 'ignored', 'edited', 'completed'})
        first, other = data[0], next(c for c in data if c['name'] == 'other-account-same-item')
        self.assertEqual(first['request'], other['request'])
        self.assertNotEqual(first['account_alias'], other['account_alias'])


if __name__ == '__main__':
    unittest.main()
