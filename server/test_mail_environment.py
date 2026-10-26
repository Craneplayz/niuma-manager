import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

import mail_delivery
import manage_environment as manager


class MailEnvironmentTests(unittest.TestCase):
    def test_idna_dependency_reaches_tests_and_the_api_image(self):
        server = Path(__file__).resolve().parent
        requirements = (server / 'requirements.txt').read_text()
        self.assertIn('idna>=3.10,<4', requirements.splitlines())
        self.assertIn('-r requirements.txt', (server / 'requirements-test.txt').read_text().splitlines())
        api_stage = (server / 'deploy/Dockerfile').read_text().split(' AS api\n', 1)[1].split('FROM caddy:', 1)[0]
        self.assertIn('COPY server/requirements.txt server/', api_stage)
        self.assertIn('uv pip install --system --no-cache -r server/requirements.txt', api_stage)

    def test_host_mail_imports_work_without_inherited_pythonpath(self):
        server = Path(__file__).resolve().parent
        script = '''import sys
sys.path.insert(0, sys.argv[1])
import idna, email_verification, mail_delivery
assert email_verification.normalize_email('Student@faß.test') == 'student@xn--fa-hia.test'
assert email_verification.normalize_email('Student@ος.test') == 'student@xn--0xag.test'
assert not mail_delivery.configured({})
'''
        process = subprocess.run([sys.executable, '-I', '-c', script, str(server)],
                                 env={}, capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_mail(self, folder, password='synthetic-test-password', sender='no-reply@mail.example.com'):
        values = dict(NIUMA_MAIL_FROM=sender, NIUMA_MAIL_PASSWORD=password)
        (folder / '.env.mail').write_text(''.join(key + '=' + value + '\n' for key, value in values.items()), encoding='utf-8')
        return values

    def test_init_creates_private_template_and_preserves_each_environment_secret(self):
        for name in manager.ENVIRONMENTS:
            folder = manager.initialize(name, self.root)
            mail = folder / '.env.mail'
            self.assertFalse(mail_delivery.configured(manager.read_mail_config(mail)))
            expected = self.write_mail(folder, password='synthetic-' + name)
            manager.initialize(name, self.root)
            self.assertEqual(manager.read_mail_config(mail), expected)
            if os.name == 'posix':
                self.assertEqual(mail.stat().st_mode & 0o777, 0o600)

    def test_old_profile_without_mail_still_checks_and_native_launches_without_inherited_secrets(self):
        folder = manager.initialize('test', self.root)
        (folder / '.env.mail').unlink()
        with patch.dict(os.environ, {'NIUMA_MAIL_FROM': 'production@mail.example.com',
                                     'NIUMA_MAIL_PASSWORD': 'synthetic-production-secret',
                                     'NIUMA_MAIL_ENV_FILE': '/production/.env.mail'}, clear=True), \
                patch.object(manager, 'git_revision', return_value='a' * 40):
            folder, config = manager.read_config('test', self.root)
            native = manager.local_environment('test', folder, config)
            self.assertFalse(any(key.startswith('NIUMA_MAIL_') for key in native))
            manager.compose_command('test', folder, config)
            self.assertEqual((folder / '.env.mail.compose').read_text(), '')

    def test_native_loads_only_target_mail_settings_preserving_literal_characters(self):
        value = 'synthetic$UNRELATED ${HOME} # slash\\quote\'"end\\'
        production = manager.initialize('production', self.root)
        self.write_mail(production, password='synthetic-production-secret')
        folder = manager.initialize('test', self.root)
        expected = self.write_mail(folder, password=value)
        inherited = {**expected, 'NIUMA_MAIL_PASSWORD': 'synthetic-production-shell-secret',
                     'NIUMA_MAIL_ENV_FILE': str(production / '.env.mail')}
        with patch.dict(os.environ, inherited, clear=True):
            native = manager.local_environment('test', folder, manager.defaults('test'))
        self.assertEqual({key: native[key] for key in expected}, expected)
        self.assertNotIn('NIUMA_MAIL_ENV_FILE', native)
        self.assertNotIn('synthetic-production-secret', json.dumps(native))

    def test_mail_file_rejects_duplicates_unknown_keys_and_control_characters_without_echoing_values(self):
        folder = manager.initialize('test', self.root)
        mail = folder / '.env.mail'
        for value in ['NIUMA_MAIL_PASSWORD=synthetic-one\nNIUMA_MAIL_PASSWORD=synthetic-two\n',
                      'NIUMA_DB=/production/database\n', 'DDL_PROVIDER=qiniu\n',
                      'NIUMA_MAIL_PASSWORD="synthetic-unclosed\n',
                      'NIUMA_MAIL_PASSWORD=synthetic\x00password\n',
                      'NIUMA_MAIL_PASSWORD=synthetic\tpassword\n']:
            mail.write_text(value)
            with self.subTest(value=value.split('=', 1)[0]), self.assertRaises(ValueError) as failure:
                manager.read_config('test', self.root)
            self.assertNotIn('synthetic', str(failure.exception))
            self.assertNotIn('/production', str(failure.exception))
        model = folder / '.env.model'
        mail.write_text('')
        model.write_text('NIUMA_MAIL_PASSWORD=synthetic-model-file-secret\n')
        with self.assertRaises(ValueError):
            manager.read_config('test', self.root)

    def test_check_only_exposes_mail_readiness_boolean(self):
        folder = manager.initialize('test', self.root)
        for ready in (False, True):
            if ready:
                self.write_mail(folder)
            output = io.StringIO()
            with patch.object(manager, 'read_config', return_value=(folder, manager.defaults('test'))), redirect_stdout(output):
                self.assertEqual(manager.main(['check', 'test']), 0)
            status = json.loads(output.getvalue())
            self.assertIs(status['mail_configured'], ready)
            self.assertEqual([key for key in status if key.startswith('mail')], ['mail_configured'])
            self.assertNotIn('no-reply', output.getvalue())
            self.assertNotIn('synthetic', output.getvalue())

    def test_managed_compose_opts_into_one_external_budget_without_sharing_user_data(self):
        for name in ('test', 'production'):
            folder = manager.initialize(name, self.root)
            self.write_mail(folder)
            with (folder / '.env.mail').open('a', encoding='utf-8') as stream:
                stream.write('NIUMA_MAIL_BUDGET_FILE=/mail-budget/quota.sqlite3\n'
                             'NIUMA_MAIL_BUDGET_DAILY=100\nNIUMA_MAIL_BUDGET_TOTAL=1000\n')
            with patch.object(manager, 'git_revision', return_value='a' * 40), \
                    patch.object(manager.mail_budget, 'Path', PureWindowsPath):
                command = manager.compose_command(name, folder, manager.defaults(name))
            self.assertEqual(command[-2:], ['-f', str(manager.DEPLOY / 'compose.mail-budget.yaml')])
            self.assertIn('/mail-budget/quota.sqlite3', (folder / '.env.mail.compose').read_text())
        override = (manager.DEPLOY / 'compose.mail-budget.yaml').read_text()
        self.assertIn('external: true', override)
        self.assertNotIn('/data', override)
        mail_file = folder / '.env.mail'
        mail_file.write_text(mail_file.read_text().replace('/mail-budget/quota.sqlite3', '/data/private-counter.db'), encoding='utf-8')
        with self.assertRaises(ValueError):
            manager.compose_command(name, folder, manager.defaults(name))

    @unittest.skipUnless(os.environ.get('NIUMA_COMPOSE_TEST_BIN') or shutil.which('docker'), 'Compose CLI required; no engine needed')
    def test_compose_preserves_mail_literals_and_isolates_every_environment(self):
        base = self.root / '$UNRELATED profile # directory'
        value = 'synthetic$UNRELATED ${HOME} # slash\\quote\'"end\\'
        for name in manager.ENVIRONMENTS:
            for transport in ('http', 'https'):
                with self.subTest(environment=name, transport=transport):
                    folder = manager.initialize(name, base)
                    expected = self.write_mail(folder, password=value + name)
                    config = manager.defaults(name)
                    if transport == 'https':
                        config.update(transport=transport, origin=f'https://example.com:{config["https_port"]}',
                                      site_address=f'example.com:{config["https_port"]}')
                    command = manager.compose_command(name, folder, config)
                    if os.environ.get('NIUMA_COMPOSE_TEST_BIN'):
                        command = [os.environ['NIUMA_COMPOSE_TEST_BIN'], *command[2:]]
                    inherited = dict(os.environ, NIUMA_MAIL_PASSWORD='synthetic-shell-production-secret',
                                     NIUMA_MAIL_FROM='production@mail.example.com', UNRELATED='wrong-shell-value')
                    with patch.dict(os.environ, inherited, clear=True):
                        process = subprocess.run(command + ['config', '--format', 'json'], env=manager.clean_environment(),
                                                 capture_output=True, text=True, encoding='utf-8', check=True, timeout=30)
                    resolved = json.loads(process.stdout.replace('$$', '$'))
                    native = manager.local_environment(name, folder, config) if name != 'production' and transport == 'http' else None
                    actual = resolved['services']['api']['environment']
                    self.assertEqual(actual['NIUMA_TRUST_PROXY_CLIENT_IP'], '1')
                    self.assertEqual({key: actual[key] for key in expected}, expected)
                    if native:
                        self.assertEqual(actual['NIUMA_MAIL_PASSWORD'], native['NIUMA_MAIL_PASSWORD'])
                    self.assertNotIn('NIUMA_MAIL_PASSWORD', resolved['services']['web'].get('environment', {}))
                    if os.name == 'posix':
                        self.assertEqual((folder / '.env.mail.compose').stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.environ.get('NIUMA_COMPOSE_TEST_BIN') or shutil.which('docker'), 'Compose CLI required; no engine needed')
    def test_raw_compose_accepts_missing_optional_mail_file(self):
        model = self.root / '.env.model'
        model.write_text('')
        for filename in ('compose.yaml', 'compose.ip.yaml'):
            env = manager.clean_environment()
            env.update(APP_ORIGIN='https://example.com', SITE_ADDRESS='example.com',
                       NIUMA_MODEL_ENV_FILE=str(model), NIUMA_MAIL_ENV_FILE=str(self.root / 'missing.env.mail'))
            command = ['docker', 'compose', '-f', str(manager.DEPLOY / filename)]
            if os.environ.get('NIUMA_COMPOSE_TEST_BIN'):
                command = [os.environ['NIUMA_COMPOSE_TEST_BIN'], *command[2:]]
            process = subprocess.run(command + ['config', '--format', 'json'], env=env, capture_output=True,
                                     text=True, encoding='utf-8', check=True, timeout=30)
            actual = json.loads(process.stdout)['services']['api']['environment']
            self.assertFalse(any(key.startswith('NIUMA_MAIL_') for key in actual))


if __name__ == '__main__':
    unittest.main()
