import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import dev_accounts
import manage_environment as manager


class TestAccountEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = manager.initialize('test', self.root / 'profiles')
        self.config = manager.defaults('test')
        self.user = dict(id='a' * 32, email='tester@niuma.test', name='测试账号', emailVerified=True)
        self.password = 'Synthetic-private-password-2026'

    def result(self, user=None, status=0):
        return subprocess.CompletedProcess([], status, stdout=json.dumps(user or self.user), stderr='')

    def create(self, **options):
        return manager.create_test_account('test', self.folder, self.config,
                                           email=self.user['email'], **options)

    def test_native_creation_uses_selected_storage_and_stdin_without_inherited_credentials(self):
        inherited = dict(NIUMA_DB='/production/database', NIUMA_ENVIRONMENT='production',
                         NIUMA_ADOPT_EXISTING_DATA='1', NIUMA_MAIL_PASSWORD='production-secret',
                         DEEPSEEK_API_KEY='production-model-secret', COMPOSE_PROJECT_NAME='niuma-production',
                         NIUMA_ADMIN_USER_IDS='administrator', NIUMA_ENABLE_SCHEDULED_SYNC='1')
        output = io.StringIO()
        with patch.object(manager, 'ROOT', self.root), patch.dict(os.environ, inherited), \
                patch.object(manager.sys, 'stdin', io.StringIO(self.password + '\n')), \
                patch.object(manager.subprocess, 'run', return_value=self.result()) as run, redirect_stdout(output):
            self.assertEqual(self.create(password_stdin=True), 0)
        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(command, [manager.sys.executable, str(self.root / 'server' / 'dev_accounts.py'), 'test'])
        self.assertEqual(options['env']['NIUMA_ENVIRONMENT'], 'test')
        self.assertEqual(options['env']['NIUMA_DB'], str(self.root / 'server' / 'data' / 'test' / 'niuma.sqlite3'))
        self.assertEqual(options['env']['NIUMA_BACKGROUND_RECOVERY'], '0')
        self.assertEqual(options['env']['NIUMA_ENABLE_SCHEDULED_SYNC'], '0')
        for key in ('NIUMA_ADOPT_EXISTING_DATA', 'NIUMA_ADMIN_USER_IDS', 'NIUMA_MAIL_PASSWORD',
                    'DEEPSEEK_API_KEY', 'COMPOSE_PROJECT_NAME'):
            self.assertNotIn(key, options['env'])
        self.assertEqual(json.loads(options['input'])['password'], self.password)
        self.assertNotIn(self.password, json.dumps(command))
        self.assertNotIn(self.password, json.dumps(options['env']))
        self.assertNotIn(self.password, output.getvalue())
        self.assertEqual(json.loads(output.getvalue()), dict(environment='test', storage='native', user=self.user))

    def test_container_creation_executes_existing_selected_api_with_no_environment_override(self):
        base = ['docker', 'compose', '--project-name', 'niuma-test']
        output = io.StringIO()
        with patch.object(manager.shutil, 'which', return_value='/usr/bin/docker'), \
                patch.object(manager, 'compose_command', return_value=base) as compose, \
                patch.object(manager.sys, 'stdin', io.StringIO(self.password)), \
                patch.object(manager.subprocess, 'run', return_value=self.result()) as run, redirect_stdout(output):
            self.assertEqual(self.create(container=True, password_stdin=True), 0)
        compose.assert_called_once_with('test', self.folder, self.config, '/usr/bin/docker')
        self.assertEqual(run.call_args.args[0], base + ['exec', '-T', 'api', 'python', 'server/dev_accounts.py', 'test'])
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['password'], self.password)
        self.assertNotIn('NIUMA_ENVIRONMENT', run.call_args.kwargs['env'])
        self.assertEqual(json.loads(output.getvalue())['storage'], 'container')
        self.assertNotIn(self.password, output.getvalue())

    def test_generation_is_explicit_and_prints_a_password_only_after_success(self):
        output = io.StringIO()
        with patch.object(manager.secrets, 'token_urlsafe', return_value=self.password), \
                patch.object(manager.subprocess, 'run', return_value=self.result()), redirect_stdout(output):
            self.create(generate_password=True)
        self.assertEqual(json.loads(output.getvalue())['password'], self.password)
        output = io.StringIO()
        with patch.object(manager.secrets, 'token_urlsafe', return_value=self.password), \
                patch.object(manager.subprocess, 'run', return_value=self.result(dict(error='account_exists'), 1)), \
                redirect_stdout(output), self.assertRaises(dev_accounts.AccountError) as failure:
            self.create(generate_password=True)
        self.assertEqual(failure.exception.code, 'account_exists')
        self.assertEqual(output.getvalue(), '')
        self.assertNotIn(self.password, str(failure.exception))

    def test_hidden_password_confirmation_and_noninteractive_refusal(self):
        with patch.object(manager.sys.stdin, 'isatty', return_value=True), \
                patch.object(manager.getpass, 'getpass', side_effect=[self.password, self.password]), \
                patch.object(manager.subprocess, 'run', return_value=self.result()), redirect_stdout(io.StringIO()):
            self.assertEqual(self.create(), 0)
        for interactive, passwords in [(False, []), (True, [self.password, 'different-password'])]:
            with self.subTest(interactive=interactive), \
                    patch.object(manager.sys.stdin, 'isatty', return_value=interactive), \
                    patch.object(manager.getpass, 'getpass', side_effect=passwords), \
                    patch.object(manager.subprocess, 'run') as run, self.assertRaises(ValueError):
                self.create()
            run.assert_not_called()

    def test_malformed_child_output_and_private_errors_are_not_echoed(self):
        for content, status in [(self.password, 1), (json.dumps(dict(error=self.password)), 1),
                                (json.dumps(dict(error={'private': self.password})), 1),
                                ('[]', 0), ('{}', 0), (json.dumps(dict(self.user, email='wrong@niuma.test')), 0)]:
            with self.subTest(status=status, shape=len(content)), \
                    patch.object(manager.subprocess, 'run', return_value=subprocess.CompletedProcess(
                        [], status, stdout=content, stderr=self.password)), \
                    patch.object(manager.sys, 'stdin', io.StringIO(self.password)), \
                    redirect_stdout(io.StringIO()) as output, self.assertRaises(dev_accounts.AccountError) as failure:
                self.create(password_stdin=True)
            self.assertEqual(failure.exception.code, 'create_failed')
            self.assertNotIn(self.password, str(failure.exception))
            self.assertEqual(output.getvalue(), '')

    def test_production_all_and_unrelated_options_are_rejected_before_configuration_or_password(self):
        for arguments in [('test-account', 'production'), ('test-account', 'all'),
                          ('check', 'test', '--generate-password'), ('local', 'test', '--email', 'tester@niuma.test'),
                          ('test-account', 'test', '--generate-password', '--password-stdin')]:
            with self.subTest(arguments=arguments), patch.object(manager, 'read_config') as read, \
                    patch.object(manager, 'create_test_account') as create, redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as failure:
                manager.main(arguments)
            self.assertEqual(failure.exception.code, 2)
            read.assert_not_called()
            create.assert_not_called()

    def test_account_command_dispatches_defaults_and_reserved_address_validation_precedes_password(self):
        with patch.object(manager, 'read_config', return_value=(self.folder, self.config)), \
                patch.object(manager, 'create_test_account', return_value=0) as create:
            self.assertEqual(manager.main(['test-account', 'test', '--container', '--generate-password']), 0)
        create.assert_called_once_with('test', self.folder, self.config, email=None, display_name=None,
                                       container=True, generate_password=True, password_stdin=False)
        for invalid in ('real@example.com', ''):
            with self.subTest(email=invalid), patch.object(manager.getpass, 'getpass') as prompt, \
                    self.assertRaises(dev_accounts.AccountError):
                manager.create_test_account('test', self.folder, self.config, email=invalid)
            prompt.assert_not_called()


if __name__ == '__main__':
    unittest.main()
