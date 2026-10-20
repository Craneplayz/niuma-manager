import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import app
from auth_test_support import registration_payload


class DeploymentConfigTests(unittest.TestCase):
    root=Path(__file__).parent/'deploy'

    def test_public_templates_have_no_model_key_or_identity_requirement(self):
        values=dict(line.split('=',1) for line in (self.root/'.env.model.example').read_text(encoding='utf-8').splitlines() if line and not line.startswith('#'))
        self.assertEqual(values['QINIU_API_KEY'],'')
        self.assertEqual(values['DDL_PROVIDER'],'qiniu')
        self.assertIn('注册不收集真实姓名、身份证或实名信息',(self.root/'README.md').read_text(encoding='utf-8'))

    def test_both_modes_preserve_explicit_data_volume_and_disable_collectors(self):
        for name in ('compose.yaml','compose.ip.yaml'):
            value=(self.root/name).read_text(encoding='utf-8')
            self.assertIn("NIUMA_ENABLE_WECHAT: '0'",value)
            self.assertIn("NIUMA_ENABLE_MANUAL_MATERIALS: '1'",value)
            self.assertIn('name: ${NIUMA_DATA_VOLUME:-niuma-app-data}',value)
            self.assertIn('- ${NIUMA_MODEL_ENV_FILE:-.env.model}',value)

    def test_http_mode_uses_loopback_api_and_non_root_web(self):
        compose=(self.root/'compose.ip.yaml').read_text(encoding='utf-8')
        self.assertIn('NIUMA_HOST: 127.0.0.1',compose)
        self.assertIn('network_mode: service:api',compose)
        self.assertIn('target: web-eci',compose)
        self.assertIn('reverse_proxy 127.0.0.1:1332',(self.root/'Caddyfile.eci').read_text(encoding='utf-8'))
        dockerfile=(self.root/'Dockerfile').read_text(encoding='utf-8').split('FROM web AS web-eci')[1]
        self.assertIn('USER 10001:10001',dockerfile)
        self.assertIn('mv /tmp/caddy /usr/bin/caddy',dockerfile)

    def test_build_context_excludes_private_configuration_and_data(self):
        rules=(self.root/'Dockerfile.dockerignore').read_text(encoding='utf-8').splitlines()
        for rule in ('**/.env','**/.env.*','**/*.key','**/*.sqlite*','server/data','crawler/data','crawler/secret','crawler/logs'):
            self.assertIn(rule,rules)
        self.assertNotIn('!wechat/',rules)


class DockerBuildContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('docker'):
            raise unittest.SkipTest('Requires Docker to verify the filtered build context')
        try:
            subprocess.run(['docker', 'info'], capture_output=True, check=True, timeout=10)
        except (subprocess.SubprocessError, OSError):
            raise unittest.SkipTest('Requires a running Docker daemon')

    def test_crawler_credentials_and_jsonl_logs_are_not_copied(self):
        with tempfile.TemporaryDirectory() as folder:
            context = Path(folder) / 'context'
            deploy = context / 'server' / 'deploy'
            deploy.mkdir(parents=True)
            dockerfile = deploy / 'Dockerfile'
            # Exercise Docker's matcher with the production ignore rules, without
            # reading local credentials, pulling a base image or building the app.
            dockerfile.write_text('FROM scratch\nCOPY crawler/ /crawler/\n', encoding='utf-8')
            shutil.copyfile(DeploymentConfigTests.root / 'Dockerfile.dockerignore',
                            deploy / 'Dockerfile.dockerignore')
            fixtures = {
                'crawler/secret': 'synthetic-user\nsynthetic-password\n',
                'crawler/logs/run.jsonl': '{"synthetic":true}\n',
                'crawler/logs/nested/run.jsonl': '{"synthetic":true}\n',
                'crawler/src/fixture.ts': 'export const synthetic = true;\n',
                'crawler/package.json': '{"name":"synthetic-crawler"}\n',
            }
            for name, content in fixtures.items():
                path = context / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')
            output = Path(folder) / 'output'
            result = subprocess.run(
                ['docker', 'build', '-f', str(dockerfile),
                 '--output', 'type=local,dest=' + str(output), str(context)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                sorted(path.relative_to(output).as_posix() for path in output.rglob('*') if path.is_file()),
                ['crawler/package.json', 'crawler/src/fixture.ts'])


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db_patch=patch.object(app,'DB',Path(self.tmp.name)/'test.sqlite3')
        self.db_patch.start()
        app.initialize(recover=False)
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.db_patch.stop()
        self.tmp.cleanup()

    def request(self,path,body=None,headers=None):
        request=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,
                        headers={'Content-Type':'application/json',**(headers or {})})
        try:
            return urlopen(request,timeout=5)
        except HTTPError as error:
            return error

    def test_health_does_not_expose_accounts_or_configuration(self):
        with self.request('/api/health') as response:
            self.assertEqual(response.status,200)
            self.assertEqual(json.load(response),{'status':'ok'})
        with self.request('/api/workspace') as response:
            self.assertEqual(response.status,401)

    def test_disabled_wechat_rejects_routes_before_reading_or_authentication(self):
        with patch.dict(app.os.environ,NIUMA_ENABLE_WECHAT='0'), patch.object(app.Handler,'body',side_effect=AssertionError('body read')):
            for path in ['/api/sources/wechat/credential','/api/sources/wechat/assets','/api/sources/wechat/imports','/api/sources/wechat/imports/job/retry']:
                with self.request(path,{'synthetic':True}) as response:
                    self.assertEqual(response.status,403)

    def test_disabled_wechat_blocks_text_import_and_retry_before_model(self):
        with patch.dict(app.os.environ,NIUMA_ENABLE_WECHAT='0'), patch.object(app.recognition,'recognize',side_effect=AssertionError('model called')):
            for path in ['/api/recognition/batches','/api/recognition/retry']:
                with self.request(path,{'records':[{'platform':'wechat','text':'synthetic'}]}) as response:
                    self.assertEqual(response.status,403)

    def test_disabled_wechat_does_not_recover_background_jobs(self):
        with patch.dict(app.os.environ,NIUMA_ENABLE_WECHAT='0',NIUMA_ENABLE_MANUAL_MATERIALS='0'), patch.object(app.wechat_media,'initialize') as initialize, patch.object(app.wechat_media,'resume') as resume:
            app.initialize()
            initialize.assert_not_called()
            resume.assert_not_called()

    def test_manual_upload_mode_never_resumes_collector_jobs(self):
        with patch.dict(app.os.environ,NIUMA_ENABLE_WECHAT='0',NIUMA_ENABLE_MANUAL_MATERIALS='1'), patch.object(app.wechat_media,'initialize') as initialize, patch.object(app.wechat_media,'resume') as resume:
            app.initialize()
            self.assertFalse(initialize.call_args.kwargs['recover'])
            resume.assert_not_called()

    def test_public_origin_cookie_and_logout(self):
        account=dict(name='部署测试',email='deployment@test.local',password='Synthetic-Test-2026!')
        with patch.dict(app.os.environ,NIUMA_ALLOWED_ORIGINS='https://demo.example.com',NIUMA_COOKIE_SECURE='1'):
            with self.request('/api/auth/email-code',dict(email=account['email'],purpose='register'),{'Origin':'https://evil.example.com'}) as response:
                self.assertEqual(response.status,403)
            def request_code(body):
                with self.request('/api/auth/email-code',body,{'Origin':'https://demo.example.com'}) as response:
                    return response.status,json.load(response)
            account=registration_payload(account,request_code)
            with self.request('/api/auth/register',account,{'Origin':'https://evil.example.com'}) as response:
                self.assertEqual(response.status,403)
            with self.request('/api/auth/register',account,{'Origin':'https://demo.example.com'}) as response:
                self.assertEqual(response.status,200)
                cookie=response.headers['Set-Cookie']
                self.assertIn('; Secure',cookie)
                self.assertIn('; HttpOnly',cookie)
                self.assertIn('; SameSite=Strict',cookie)
            with self.request('/api/auth/logout',{}, {'Origin':'https://demo.example.com','Cookie':cookie.split(';')[0]}) as response:
                self.assertEqual(response.status,200)
                self.assertIn('Max-Age=0; Secure',response.headers['Set-Cookie'])

    def test_local_development_defaults_remain_unchanged(self):
        with patch.dict(app.os.environ,{},clear=True):
            self.assertEqual(app.allowed_origins(),['http://localhost:1331','http://127.0.0.1:1331'])
            self.assertNotIn('Secure',app.session_cookie('test',86400))
            self.assertIn('HttpOnly',app.session_cookie('test',86400))

    def test_forwarded_headers_do_not_authorize_an_origin(self):
        with patch.dict(app.os.environ,NIUMA_ALLOWED_ORIGINS='https://demo.example.com'):
            with self.request('/api/auth/login',{}, {'Origin':'https://evil.example.com',
                 'X-Forwarded-Host':'demo.example.com','X-Forwarded-Proto':'https'}) as response:
                self.assertEqual(response.status,403)
