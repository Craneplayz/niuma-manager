"""Opt-in Docker smoke test: fresh labelled volumes, synthetic accounts, no model calls."""
import json
import base64
import hashlib
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import manage_environment as manager
import release_images as release
import test_environment_isolation as environment_tests


@unittest.skipUnless(shutil.which('docker') and all(os.environ.get('NIUMA_CONTAINER_TEST_' + name)
                     for name in ('API', 'WEB_IP', 'WEB_HTTPS')), 'Requires explicit locally built smoke-test images')
class ContainerEnvironmentTests(environment_tests.LiveEnvironmentIsolationTests):
    def command(self, args, check=True):
        env = manager.clean_environment()
        for name, command in getattr(self, 'stacks', {}).items():
            if args[:len(command)] == command:
                env.update(self.compose_envs[name])
                break
        return subprocess.run(args, env=env, capture_output=True,
                              text=True, encoding='utf-8', check=check, timeout=180)

    def free_port(self):
        while True:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            if port not in self.used_ports:
                self.used_ports.add(port)
                return port

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.prefix = 'niuma-smoke-' + secrets.token_hex(8)
        self.stacks, self.compose_envs, self.volumes, self.urls = {}, {}, {}, {}
        self.used_ports = set()
        self.addCleanup(self.cleanup)
        # The HTTPS endpoint is a local synthetic fixture using Caddy's local CA.
        self.tls = ssl._create_unverified_context()
        for name in ('development', 'test', 'production'):
            folder = manager.initialize(name, Path(self.temp.name))
            config = manager.defaults(name)
            config.update(port=self.free_port(), api_image=os.environ['NIUMA_CONTAINER_TEST_API'],
                          web_image=os.environ['NIUMA_CONTAINER_TEST_WEB_IP'], background_recovery=False)
            config['origin'] = f'http://127.0.0.1:{config["port"]}'
            if name == 'production':
                config.update(transport='https', https_port=self.free_port(),
                              web_image=os.environ['NIUMA_CONTAINER_TEST_WEB_HTTPS'])
                # A hostname sends TLS SNI; Python deliberately omits SNI for IP
                # literals, which cannot select Caddy's local test certificate.
                config['site_address'] = f'localhost:{config["https_port"]}'
                config['origin'] = 'https://' + config['site_address']
            self.urls[name] = config['origin']
            # Validate normal profile rules first, then assign unique disposable
            # Docker names so this test never touches an existing environment.
            (folder / 'config.json').write_text(json.dumps(config), encoding='utf-8')
            _, config = manager.read_config(name, Path(self.temp.name))
            volume = self.prefix + '-' + name + '-data'
            existing = self.command(['docker', 'volume', 'ls', '--format', '{{.Name}}']).stdout.splitlines()
            self.assertNotIn(volume, existing)
            self.command(['docker', 'volume', 'create', '--label', 'niuma.smoke=' + self.prefix, volume])
            self.volumes[name] = volume
            config.update(data_volume=volume, external_data_volume=True)
            # Keep interpolation and the environment passed to Compose on the
            # same model-settings snapshot, just as the managed CLI does.
            model_values = manager.read_model_config(folder / '.env.model')
            command = manager.compose_command(name, folder, config, model_values=model_values)
            command[command.index('--project-name') + 1] = self.prefix + '-' + name
            self.stacks[name] = command
            self.compose_envs[name] = manager.compose_environment(name, folder, config, model_values=model_values)
            self.command(command + ['up', '-d', '--no-build', '--wait', '--wait-timeout', '90'])
            self.wait_ready(name)

    def wait_ready(self, name):
        deadline = time.monotonic() + 30
        while True:
            try:
                with self.open(self.urls[name] + '/api/health') as response:
                    self.assertEqual(json.load(response), {'status': 'ok'})
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)

    def open(self, request):
        return urlopen(request, timeout=10, context=self.tls)

    def request(self, environment, path, body=None, cookie=None, method=None):
        headers = {'Content-Type': 'application/json', 'Origin': self.urls[environment]}
        if cookie:
            headers['Cookie'] = cookie
        request = Request(self.urls[environment] + path, headers=headers, method=method,
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            response = self.open(request)
        except HTTPError as error:
            response = error
        with response:
            return response.status, dict(response.headers), json.load(response)

    def read_asset(self, name, user_id, file_ref):
        script = '''import sys, base64
sys.path.insert(0, 'server')
from wechat_media import account_asset_loader
data = account_asset_loader('/data/niuma.sqlite3', sys.argv[1])(sys.argv[2], max_bytes=10000, timeout_seconds=5)
print(base64.b64encode(data).decode())
'''
        result = self.command(self.stacks[name]+['exec', '-T', 'api', 'python', '-c', script, user_id, file_ref], check=False)
        if result.returncode:
            # Reject only an actual ownership/missing-reference failure, not an unrelated crash.
            self.assertIn('file_ref_unavailable', result.stderr)
            raise FileNotFoundError('file_ref_unavailable')
        return base64.b64decode(result.stdout.strip(), validate=True)

    def test_accounts_sessions_writes_and_version_are_isolated(self):
        super().test_accounts_sessions_writes_and_version_are_isolated()
        revision = manager.git_revision()
        for name in self.urls:
            version = self.request(name, '/api/version')[2]
            self.assertEqual(version['revision'], revision)
            with self.open(self.urls[name]) as response:
                html = response.read().decode()
            script = re.search(r'<script\b[^>]*\bsrc="([^"]+)"', html)
            self.assertIsNotNone(script)
            self.assertTrue(script[1].startswith('/assets/'))
            with self.open(self.urls[name] + script[1]) as response:
                self.assertIn(revision, response.read().decode())

        credentials = {'email': 'same@synthetic.test', 'password': 'Synthetic-2026!'}
        status, headers, _ = self.request('production', '/api/auth/login', credentials)
        self.assertEqual(status, 200)
        self.assertIn('; Secure', headers['Set-Cookie'])
        # Use real Docker metadata; only substitute this test's random project name.
        original_query = release._query
        def isolated_query(docker, env, *args):
            args = tuple('label=com.docker.compose.project='+self.prefix+'-test'
                         if value == 'label=com.docker.compose.project=niuma-test' else value for value in args)
            return original_query(docker, env, *args)
        config = dict(manager.defaults('test'), api_image=os.environ['NIUMA_CONTAINER_TEST_API'],
                      web_image=os.environ['NIUMA_CONTAINER_TEST_WEB_IP'])
        receipt_path = Path(self.temp.name)/'accepted-images.json'
        with patch.object(release, '_query', side_effect=isolated_query):
            accepted = release.record_acceptance('docker', manager.clean_environment(), config, receipt_path, 'Synthetic container acceptance')
        pinned = release.verified_images('docker', manager.clean_environment(), config, receipt_path)
        self.assertEqual(pinned, {service+'_image':image['id'] for service,image in accepted['images'].items()})
        config['web_image'] = os.environ['NIUMA_CONTAINER_TEST_WEB_HTTPS']
        with self.assertRaisesRegex(ValueError, 'does not match'):
            release.verified_images('docker', manager.clean_environment(), config, receipt_path)
        for name in self.urls:
            # Recreate every environment, including the HTTP shared network namespace.
            self.command(self.stacks[name] + ['up', '-d', '--no-build', '--force-recreate',
                                              '--wait', '--wait-timeout', '90', 'api', 'web'])
            self.wait_ready(name)
            status, headers, _ = self.request(name, '/api/auth/login', credentials)
            self.assertEqual(status, 200)
            cookie = headers['Set-Cookie'].split(';')[0]
            self.cookies[name] = cookie
            self.assertEqual(self.request(name, '/api/workspace', cookie=cookie)[2], self.expected[name])
            self.assertEqual(self.request(name, '/api/sources/credentials', cookie=cookie)[2],
                             dict(configured=True, username='synthetic-'+name,
                                  source_account_id=hashlib.sha256(('synthetic-'+name).encode()).hexdigest()))
            receipt = self.receipts[name]
            content = self.read_asset(name, self.user_ids[name], receipt['file_ref'])
            self.assertEqual(content, ('Synthetic attachment '+name+'\n').encode())
            self.assertEqual(hashlib.sha256(content).hexdigest(), receipt['sha256'])
            self.assertEqual(len(content), receipt['size_bytes'])
            verify_secret = '''import sys, sqlite3
from pathlib import Path
from cryptography.fernet import Fernet
with sqlite3.connect('/data/niuma.sqlite3') as db:
    row = db.execute('SELECT secret FROM source_credentials WHERE user_id=?', (sys.argv[1],)).fetchone()
assert Fernet(Path('/data/credential.key').read_bytes()).decrypt(row[0]).decode() == 'Synthetic-Only!'
'''
            self.command(self.stacks[name]+['exec', '-T', 'api', 'python', '-c', verify_secret, self.user_ids[name]])
        # Recreated services must still reject tokens and file references from the other two environments.
        for source in self.urls:
            for target in self.urls:
                if source == target: continue
                forged = 'niuma_session_'+target+'='+self.cookies[source].split('=',1)[1]
                self.assertEqual(self.request(target,'/api/auth/me',cookie=forged)[0],401)
                with self.assertRaises(FileNotFoundError):
                    self.read_asset(target,self.user_ids[target],self.receipts[source]['file_ref'])
        seeded = self.command(self.stacks['production'] + ['exec', '-T', 'api', 'python', 'server/app.py', '--seed'], check=False)
        self.assertNotEqual(seeded.returncode, 0)
        self.assertIn('disabled in production', seeded.stderr)
        # Even a direct Docker invocation cannot re-open another environment's data.
        wrong = self.command(['docker', 'run', '--rm', '--network', 'none',
                              '-e', 'NIUMA_ENVIRONMENT=test', '-v', self.volumes['production'] + ':/data',
                              os.environ['NIUMA_CONTAINER_TEST_API'], 'python', '-c',
                              "import sys; sys.path.insert(0, 'server'); import runtime_environment as r; r.validate_storage(r.database_path())"], check=False)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn('another environment', wrong.stderr)

    def cleanup(self):
        for name, command in reversed(list(self.stacks.items())):
            try:
                for service in ('api', 'web'):
                    output = self.command(command + ['logs', '--no-color', '--tail', '30', service], check=False)
                    print(f'[{self.prefix}/{name}/{service}]\n{output.stdout}{output.stderr}')
                # Only this random project and its synthetic TLS volumes are removed.
                self.command(command + ['down', '--volumes', '--remove-orphans'])
            except subprocess.SubprocessError as error:
                print('Smoke cleanup failed:', type(error).__name__)
        for volume in self.volumes.values():
            identity = self.command(['docker', 'volume', 'inspect', '--format', '{{index .Labels "niuma.smoke"}}', volume], check=False)
            if identity.returncode == 0 and identity.stdout.strip() == self.prefix:
                self.command(['docker', 'volume', 'rm', volume], check=False)
        self.temp.cleanup()
