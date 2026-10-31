import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import manage_environment as manager
import release_images as release


class ReleaseImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'accepted-images.json'
        self.pair = {service: dict(id='sha256:' + digit * 64, revision='c' * 40)
                     for service, digit in [('api', 'a'), ('web', 'b')]}
        self.test_config = dict(manager.defaults('test'), api_image='registry/api:candidate', web_image='registry/web:candidate')
        self.production = dict(manager.defaults('production'), api_image='registry/api:release', web_image='registry/web:release')

    def image(self, docker, env, ref):
        service = 'api' if '/api:' in ref or ref == self.pair['api']['id'] else 'web'
        return copy.deepcopy(self.pair[service])

    def accept(self):
        with patch.object(release, '_running_pair', return_value=self.pair), patch.object(release, '_image', side_effect=self.image):
            return release.record_acceptance('docker', {}, self.test_config, self.path, 'Synthetic acceptance passed')

    def test_acceptance_persists_actual_pair_and_production_pins_ids(self):
        record = self.accept()
        self.assertEqual(record['images'], self.pair)
        self.assertEqual(json.loads(self.path.read_text()), record)
        with patch.object(release, '_image', side_effect=self.image):
            pinned = release.verified_images('docker', {}, self.production, self.path)
        self.assertEqual(pinned, {key + '_image': value['id'] for key, value in self.pair.items()})

    def test_missing_corrupt_or_wrong_environment_receipt_fails_closed(self):
        with self.assertRaises(ValueError):
            release.verified_images('docker', {}, self.production, self.path)
        record = self.accept()
        for bad in [[], {}, None, dict(record, environment='production'), dict(record, transport='https'),
                    dict(record, images={'api': self.pair['api']}), dict(record, evidence=''),
                    dict(record, images={'api': self.pair['api'], 'web': dict(self.pair['web'], revision='d' * 40)})]:
            self.path.write_text(json.dumps(bad))
            with self.subTest(record=bad), self.assertRaises(ValueError):
                release.verified_images('docker', {}, self.production, self.path)

    def test_tags_cannot_silently_select_latest_or_change_after_acceptance(self):
        self.accept()
        for ref in ['', 'registry/api', 'registry:5000/api', 'registry/api:latest', 'registry/api:LATEST']:
            with self.subTest(ref=ref), self.assertRaises(ValueError), patch.object(release, '_image') as inspect:
                release.verified_images('docker', {}, dict(self.production, api_image=ref), self.path)
            inspect.assert_not_called()
        for service in ('api', 'web'):
            with patch.object(release, '_image', side_effect=lambda d, e, r: dict(self.image(d, e, r),
                              id='sha256:' + 'f' * 64) if service in r else self.image(d, e, r)):
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    release.verified_images('docker', {}, self.production, self.path)

    def test_acceptance_does_not_certify_undeployed_configuration(self):
        self.path.write_text('preserved record')
        with patch.object(release, '_running_pair', return_value=self.pair), \
                patch.object(release, '_image', return_value=dict(self.pair['api'], id='sha256:' + 'e' * 64)):
            with self.assertRaisesRegex(ValueError, 'differs'):
                release.record_acceptance('docker', {}, self.test_config, self.path, 'synthetic')
        self.assertEqual(self.path.read_text(), 'preserved record')

    def test_recording_requires_healthy_matching_running_containers(self):
        def query(docker, env, *args):
            if args[0] == 'ps': return '0123456789ab'
            return json.dumps(self.identity)
        self.identity = dict(image=self.pair['api']['id'], state=dict(Running=True, Health=dict(Status='healthy')), transport='http')
        with patch.object(release, '_query', side_effect=query), patch.object(release, '_image', return_value=self.pair['api']):
            self.assertEqual(set(release._running_pair('docker', {}, 'http')), {'api', 'web'})
            for change in [dict(state={'Running': False}), dict(state={'Running': True}),
                           dict(state={'Running': True, 'Health': {'Status': 'starting'}}), dict(transport='https')]:
                saved = self.identity
                self.identity = dict(saved, **change)
                with self.assertRaises(ValueError): release._running_pair('docker', {}, 'http')
                self.identity = saved
        with patch.object(release, '_query', return_value=''):
            with self.assertRaises(ValueError): release._running_pair('docker', {}, 'http')
        with patch.object(release, '_query', side_effect=query), patch.object(release, '_image', side_effect=[self.pair['api'], dict(self.pair['web'], revision='d'*40)]):
            with self.assertRaisesRegex(ValueError, 'different source'): release._running_pair('docker', {}, 'http')

    def test_running_pair_ignores_helpers_but_rejects_duplicate_compose_services(self):
        duplicate = False
        calls = []
        def query(docker, env, *args):
            calls.append(args)
            if args[0] == 'ps':
                real = '0123456789ab' if 'label=com.docker.compose.service=api' in args else 'fedcba987654'
                scoped = ('label=com.docker.compose.config-hash' in args and
                          'label=com.docker.compose.oneoff=False' in args)
                return real + ('\n' + 'd' * 12 if duplicate or not scoped else '')
            service = 'api' if args[-1] == '0123456789ab' else 'web'
            return json.dumps(dict(image=self.pair[service]['id'],
                                   state=dict(Running=True, Health=dict(Status='healthy')), transport='http'))
        with patch.object(release, '_query', side_effect=query), patch.object(release, '_image', side_effect=self.image):
            self.assertEqual(release._running_pair('docker', {}, 'http'), self.pair)
            self.assertTrue(all('d' * 12 not in args for args in calls if args[0] == 'inspect'))
            duplicate = True
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                release._running_pair('docker', {}, 'http')

    def test_production_entry_cannot_bypass_acceptance_and_pins_compose(self):
        with patch.object(manager, 'read_config', return_value=(self.path.parent, self.production, {})), \
                patch.object(manager.shutil, 'which', return_value='docker'), \
                patch.object(manager, 'verified_images', side_effect=ValueError('missing acceptance')), \
                patch.object(manager, 'compose_command') as compose, patch.object(manager.subprocess, 'call') as call:
            with self.assertRaisesRegex(ValueError, 'missing acceptance'): manager.main(['up', 'production'])
            compose.assert_not_called()
            call.assert_not_called()
        pinned = {key + '_image': value['id'] for key, value in self.pair.items()}
        with patch.object(manager, 'read_config', return_value=(self.path.parent, self.production, {})), \
                patch.object(manager.shutil, 'which', return_value='docker'), \
                patch.object(manager, 'verified_images', return_value=pinned) as verified, \
                patch.object(manager, 'compose_command', return_value=['docker', 'compose']) as compose, \
                patch.object(manager.subprocess, 'run'), patch.object(manager.subprocess, 'check_output', return_value=''), \
                patch.object(manager.subprocess, 'call', return_value=0) as call:
            manager.main(['up', 'production', '--acceptance', str(self.path)])
            self.assertEqual(verified.call_args.args[-1], self.path)
            for key, value in pinned.items(): self.assertEqual(compose.call_args.args[2][key], value)
            self.assertIn('--no-build', call.call_args.args[0])
            self.assertEqual(call.call_args.args[0][-5:], ['--pull', 'never', '--force-recreate', 'api', 'web'])

    def test_missing_managed_model_file_fails_closed_before_compose(self):
        folder = manager.initialize('production', self.path.parent)
        (folder / '.env.model').unlink()
        (folder / 'config.json').write_text(json.dumps(self.production), encoding='utf-8')
        pinned = {key + '_image': value['id'] for key, value in self.pair.items()}
        read_profile = manager.read_config
        with patch.object(manager, 'read_config', side_effect=lambda name, include_model_values=False:
                          read_profile(name, self.path.parent, include_model_values=include_model_values)), \
                patch.object(manager.shutil, 'which', return_value='docker'), \
                patch.object(manager, 'verified_images', return_value=pinned), \
                patch.object(manager, 'compose_command') as compose:
            with self.assertRaises(FileNotFoundError):
                manager.main(['up', 'production', '--acceptance', str(self.path)])
        compose.assert_not_called()
