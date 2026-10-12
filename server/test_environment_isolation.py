import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import manage_environment as manager
import runtime_environment as runtime
from ddl_text.codex_client import live_options


class EnvironmentBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_wrong_environment_cannot_open_or_mutate_data(self):
        db = self.root / 'data' / 'niuma.sqlite3'
        with patch.dict(os.environ, NIUMA_ENVIRONMENT='production'):
            runtime.validate_storage(db)
        db.write_bytes(b'production sentinel')
        marker = db.parent / '.niuma-environment.json'
        before = marker.read_bytes()
        with patch.dict(os.environ, NIUMA_ENVIRONMENT='test', NIUMA_ADOPT_EXISTING_DATA='1'):
            with self.assertRaisesRegex(ValueError, 'another environment'):
                runtime.validate_storage(db)
        self.assertEqual(db.read_bytes(), b'production sentinel')
        self.assertEqual(marker.read_bytes(), before)

    def test_legacy_data_needs_explicit_production_adoption(self):
        db = self.root / 'niuma.sqlite3'
        db.write_bytes(b'existing data')
        for name in runtime.ENVIRONMENTS:
            with patch.dict(os.environ, NIUMA_ENVIRONMENT=name, NIUMA_ADOPT_EXISTING_DATA='0'):
                with self.assertRaisesRegex(ValueError, 'Unlabelled existing data'):
                    runtime.validate_storage(db)
        with patch.dict(os.environ, NIUMA_ENVIRONMENT='test', NIUMA_ADOPT_EXISTING_DATA='1'):
            with self.assertRaises(ValueError):
                runtime.validate_storage(db)
        with patch.dict(os.environ, NIUMA_ENVIRONMENT='production', NIUMA_ADOPT_EXISTING_DATA='1'):
            runtime.validate_storage(db)
        self.assertEqual(db.read_bytes(), b'existing data')

    def test_legacy_startup_cannot_bypass_a_storage_marker(self):
        for name in runtime.ENVIRONMENTS:
            db = self.root / name / 'niuma.sqlite3'
            with patch.dict(os.environ, {'NIUMA_ENVIRONMENT': name}, clear=True):
                runtime.validate_storage(db)
            db.write_bytes(b'existing data')
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, 'explicit NIUMA_ENVIRONMENT'):
                    runtime.validate_storage(db)
            self.assertEqual(db.read_bytes(), b'existing data')
        with patch.dict(os.environ, {}, clear=True):
            runtime.validate_storage(self.root / 'legacy' / 'db.sqlite3')
        self.assertFalse((self.root / 'legacy').exists())

    def test_managed_model_settings_never_fall_back_to_root_secret(self):
        (self.root / '.env').write_text('DDL_PROVIDER=qiniu\nQINIU_API_KEY=synthetic-production-secret\n')
        with patch.object(runtime, 'ROOT', self.root), patch.dict(os.environ, {'NIUMA_ENVIRONMENT':'test'}, clear=True):
            self.assertNotEqual(runtime.model_env_path(), self.root / '.env')
            self.assertEqual(live_options(runtime.model_env_path())['key'], '')
            self.assertFalse(runtime.allow_background_recovery())
        with patch.object(runtime, 'ROOT', self.root), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(runtime.model_env_path(), self.root / '.env')

    def test_profiles_reject_shared_volumes_and_model_routing_overrides(self):
        for name in runtime.ENVIRONMENTS:
            manager.initialize(name, self.root)
            manager.read_config(name, self.root)
        path = self.root / 'test' / 'config.json'
        config = json.loads(path.read_text())
        config['data_volume'] = 'niuma-production-data'
        path.write_text(json.dumps(config))
        with self.assertRaises(ValueError):
            manager.read_config('test', self.root)
        path.write_text(json.dumps(manager.defaults('test')))
        model = self.root / 'test' / '.env.model'
        model.write_text('QINIU_API_KEY=synthetic\nNIUMA_DB=/production/database\n')
        with self.assertRaisesRegex(ValueError, 'provider settings only'):
            manager.read_config('test', self.root)

    def test_environment_init_preserves_existing_secrets_and_shell_cannot_override(self):
        folder = manager.initialize('test', self.root)
        (folder / '.env.model').write_text('QINIU_API_KEY=synthetic-test-key\n')
        manager.initialize('test', self.root)
        self.assertIn('synthetic-test-key', (folder / '.env.model').read_text())
        inherited = {'NIUMA_DB':'production', 'API_IMAGE':'production:latest', 'WEB_IMAGE':'production:latest',
                     'COMPOSE_PROJECT_NAME':'production','QINIU_API_KEY':'production-secret','APP_ORIGIN':'https://production.example',
                     'TEXT_HARNESS_URL':'https://production.example/model','PATH':'synthetic'}
        with patch.dict(os.environ, inherited, clear=True):
            child = manager.local_environment('test', folder, manager.defaults('test'))
        for key in inherited:
            if key not in ('PATH', 'NIUMA_DB', 'APP_ORIGIN'):
                self.assertNotIn(key, child)
        self.assertNotIn('APP_ORIGIN',child)
        self.assertIn(str(Path('server')/'data'/'test'),child['NIUMA_DB'])
        self.assertEqual(child['NIUMA_BACKGROUND_RECOVERY'],'0')

    def test_model_files_use_the_native_literal_parser_and_fail_early(self):
        folder = manager.initialize('test', self.root)
        model = folder / '.env.model'
        text = 'DDL_PROVIDER=qiniu\nQINIU_API_KEY=synthetic$UNRELATED ${HOME} # literal\nDDL_MODEL="synthetic\\model"\n'
        model.write_text(text)
        manager.read_config('test', self.root)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(manager.read_model_config(model)['QINIU_API_KEY'], live_options(model)['key'])
        for invalid in ['QINIU_API_KEY="unclosed\n', 'QINIU_API_KEY=a\nQINIU_API_KEY=b\n',
                        'NIUMA_DB=other-environment\n', 'QINIU_API_KEY=contains\x00null\n']:
            with self.subTest(invalid=invalid):
                model.write_text(invalid)
                with self.assertRaises(ValueError):
                    manager.read_config('test', self.root)

    def test_published_ports_and_origin_must_agree(self):
        folder = manager.initialize('test', self.root)
        for origin in ['http://localhost:bad', 'http://localhost:65536', 'http://localhost:1532',
                       'http://localhost:1531#fragment', 'http://local$HOST:1531']:
            config = manager.defaults('test')
            config['origin'] = origin
            (folder / 'config.json').write_text(json.dumps(config))
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                manager.read_config('test', self.root)
        config = manager.defaults('test')
        config.update(transport='https', origin='https://test.example.com:1543', site_address='test.example.com:1543')
        (folder / 'config.json').write_text(json.dumps(config))
        manager.read_config('test', self.root)
        config['port'] = config['https_port']
        (folder / 'config.json').write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'ports must differ'):
            manager.read_config('test', self.root)

    def test_local_servers_reject_a_remote_origin(self):
        config = manager.defaults('test')
        config['origin'] = 'http://production.example.com:1531'
        with self.assertRaisesRegex(ValueError, 'Native origin'):
            manager.local_environment('test', self.root, config)

    def test_origin_normalization_matches_browser_login_requests(self):
        folder = manager.initialize('production', self.root)
        path = folder / 'config.json'
        config = manager.defaults('production')
        config.update(transport='https', origin='https://EXAMPLE.com:443', site_address='EXAMPLE.com:443')
        path.write_text(json.dumps(config))
        _, actual = manager.read_config('production', self.root)
        self.assertEqual(actual['origin'], 'https://example.com')
        self.assertEqual(actual['site_address'], 'example.com')
        config.update(origin='https://example.com:0', site_address='example.com:0')
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'between 1 and 65535'):
            manager.read_config('production', self.root)

    def test_production_requires_external_separate_volume_even_without_other_profiles(self):
        folder = manager.initialize('production', self.root)
        for changes in [{'external_data_volume': False}, {'data_volume': 'niuma-test-data'},
                        {'data_volume': 'niuma-development-data'}]:
            config = {**manager.defaults('production'), **changes}
            (folder / 'config.json').write_text(json.dumps(config))
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'external volume'):
                manager.read_config('production', self.root)

    def test_build_cannot_overwrite_a_verified_release(self):
        with patch.object(manager, 'read_config') as read, patch.object(manager.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'promote'):
                manager.main(['build', 'production'])
            read.assert_not_called()
            config = manager.defaults('test')
            config['api_image'] = 'registry.example.com/api:verified'
            read.return_value = (self.root, config)
            with self.assertRaisesRegex(ValueError, 'overwrite a release tag'):
                manager.main(['build', 'test'])
            run.assert_not_called()

    def test_stopping_only_targets_the_selected_project_without_reading_broken_config(self):
        with patch.object(manager, 'read_config', side_effect=AssertionError('config read')), \
                patch.object(manager.shutil, 'which', return_value='docker'), \
                patch.object(manager.subprocess, 'check_output', return_value='0123456789ab\n') as query, \
                patch.object(manager.subprocess, 'call', return_value=0) as call:
            self.assertEqual(manager.main(['stop', 'test']), 0)
            self.assertEqual(query.call_args.args[0], ['docker', 'ps', '--filter',
                             'label=com.docker.compose.project=niuma-test', '--quiet'])
            self.assertEqual(call.call_args.args[0], ['docker', 'stop', '0123456789ab'])
            query.return_value = ''
            call.reset_mock()
            self.assertEqual(manager.main(['stop', 'test']), 0)
            call.assert_not_called()
            query.return_value = 'unexpected-container-name'
            with self.assertRaises(ValueError):
                manager.main(['stop', 'test'])
            call.assert_not_called()

    @unittest.skipUnless(os.environ.get('NIUMA_COMPOSE_TEST_BIN') or shutil.which('docker'),
                         'Compose CLI required for configuration integration test (no engine needed)')
    def test_real_compose_preserves_model_literals_paths_and_all_environment_ports(self):
        # config serializes $ as $$ so its output can be used as a Compose input:
        # docker/compose cmd/compose/config.go runConfig; decode that output layer.
        base = self.root / '$UNRELATED profile # directory'
        value = 'synthetic$UNRELATED ${HOME} # slash\\quote\'"end\\'
        for name in runtime.ENVIRONMENTS:
            for transport in ('http', 'https'):
                with self.subTest(environment=name, transport=transport):
                    folder = manager.initialize(name, base)
                    (folder / '.env.model').write_text('QINIU_API_KEY=' + value + '\n')
                    config = manager.defaults(name)
                    if transport == 'https':
                        config.update(transport=transport, origin=f'https://example.com:{config["https_port"]}',
                                      site_address=f'example.com:{config["https_port"]}')
                    (folder / 'config.json').write_text(json.dumps(config))
                    folder, config = manager.read_config(name, base)
                    command = manager.compose_command(name, folder, config)
                    if os.environ.get('NIUMA_COMPOSE_TEST_BIN'):
                        command = [os.environ['NIUMA_COMPOSE_TEST_BIN'], *command[2:]]
                    env = manager.clean_environment()
                    env['UNRELATED'] = 'synthetic-shell-value-must-not-win'
                    process = subprocess.run(command + ['config', '--format', 'json'], env=env,
                                             capture_output=True, text=True, encoding='utf-8', check=True, timeout=30)
                    resolved = json.loads(process.stdout.replace('$$', '$'))
                    self.assertEqual(resolved['name'], 'niuma-' + name)
                    api = resolved['services']['api']
                    self.assertEqual(api['environment']['QINIU_API_KEY'], value)
                    self.assertEqual(api['environment']['NIUMA_ENVIRONMENT'], name)
                    self.assertEqual(resolved['volumes']['app-data']['name'], config['data_volume'])
                    if transport == 'https':
                        web = resolved['services']['web']
                        self.assertEqual(web['environment']['SITE_ADDRESS'], config['site_address'])
                        self.assertTrue(any(str(p['published']) == str(config['https_port'])
                                            and p['target'] == config['https_port'] for p in web['ports']))
                    else:
                        self.assertEqual(api['ports'][0]['target'], 1331)
                        self.assertEqual(str(api['ports'][0]['published']), str(config['port']))
                    if os.name != 'nt':
                        self.assertEqual((folder / '.env.model.compose').stat().st_mode & 0o777, 0o600)

    def test_production_cannot_be_seeded(self):
        import app
        with patch.dict(os.environ, NIUMA_ENVIRONMENT='production'), patch.object(app,'initialize') as initialize:
            with self.assertRaisesRegex(ValueError,'disabled in production'):
                app.seed()
            initialize.assert_not_called()

    def test_nonproduction_recovers_job_bookkeeping_without_starting_model_work(self):
        import app
        with patch.dict(os.environ, NIUMA_ENVIRONMENT='test', NIUMA_BACKGROUND_RECOVERY='0', NIUMA_ENABLE_WECHAT='1'), \
                patch.object(app,'DB',self.root/'data'/'test.sqlite3'), \
                patch.object(app.wechat_media,'initialize') as initialize, \
                patch.object(app.wechat_media,'resume') as resume, \
                patch.object(app.reprocessing,'initialize') as reprocessing:
            app.initialize()
            self.assertTrue(initialize.call_args.kwargs['recover'])
            self.assertTrue(reprocessing.call_args.kwargs['recover'])
            resume.assert_not_called()


class LiveEnvironmentIsolationTests(unittest.TestCase):
    """Run two real API processes; no model calls, production files or live accounts."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.children = []
        self.logs = []
        self.urls = {}
        self.addCleanup(self.cleanup)
        for name in ('development','test'):
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0))
                port=probe.getsockname()[1]
            folder=Path(self.temp.name)/name
            folder.mkdir()
            log=(folder/'process.log').open('w',encoding='utf-8')
            self.logs.append(log)
            env=manager.clean_environment()
            env.update(NIUMA_ENVIRONMENT=name,NIUMA_DB=str(folder/'data'/'niuma.sqlite3'),NIUMA_PORT=str(port),
                       NIUMA_HOST='127.0.0.1',NIUMA_ALLOWED_ORIGINS=f'http://127.0.0.1:{port}',
                       NIUMA_ENABLE_WECHAT='0',NIUMA_ENABLE_MANUAL_MATERIALS='0',PYTHONUTF8='1')
            process=subprocess.Popen([sys.executable,'-u',str(manager.ROOT/'server'/'app.py')],
                                     cwd=manager.ROOT,env=env,stdout=log,stderr=log)
            self.children.append(process)
            self.urls[name]=f'http://127.0.0.1:{port}'
            for attempt in range(100):
                if process.poll() is not None:
                    self.fail('Synthetic API startup failed; inspect temporary process log')
                try:
                    with urlopen(self.urls[name]+'/api/health',timeout=0.3) as response:
                        if response.status==200:break
                except OSError:time.sleep(0.05)
            else:self.fail('Synthetic API startup timed out')

    def cleanup(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait(timeout=5)
        for log in self.logs:log.close()
        self.temp.cleanup()

    def request(self,environment,path,body=None,cookie=None,method=None):
        headers={'Content-Type':'application/json','Origin':self.urls[environment]}
        if cookie:headers['Cookie']=cookie
        req=Request(self.urls[environment]+path,data=json.dumps(body).encode() if body is not None else None,
                    headers=headers,method=method)
        try:response=urlopen(req,timeout=5)
        except HTTPError as error:response=error
        with response:return response.status,dict(response.headers),json.load(response)

    def test_accounts_sessions_writes_and_version_are_isolated(self):
        cookies={}
        for name in self.urls:
            status,headers,_=self.request(name,'/api/auth/register',{'email':'same@synthetic.test','password':'Synthetic-2026!','name':name})
            self.assertEqual(status,200)
            cookies[name]=headers['Set-Cookie'].split(';')[0]
            self.assertTrue(cookies[name].startswith('niuma_session_'+name+'='))
            status,_,version=self.request(name,'/api/version')
            self.assertEqual(status,200)
            self.assertEqual(version['environment'],name)
            self.assertFalse(version['background_recovery'])
            self.assertEqual(set(version),{'environment','managed','revision','background_recovery','scheduled_sync_available'})
        self.assertEqual(self.request('test','/api/auth/me',cookie=cookies['development'])[0],401)
        forged='niuma_session_test='+cookies['development'].split('=',1)[1]
        self.assertEqual(self.request('test','/api/auth/me',cookie=forged)[0],401)
        status,_,_=self.request('development','/api/data/preferences',{'value':{'askCompletionMinutes':False},'version':0},cookies['development'],'PUT')
        self.assertEqual(status,200)
        self.assertIn('preferences',self.request('development','/api/workspace',cookie=cookies['development'])[2])
        self.assertNotIn('preferences',self.request('test','/api/workspace',cookie=cookies['test'])[2])
        self.assertEqual(self.request('development','/api/sources/credentials',{'username':'synthetic-student','password':'Synthetic-Only!'},cookies['development'])[0],200)
        self.assertFalse(self.request('test','/api/sources/credentials',cookie=cookies['test'])[2]['configured'])
        self.request('test','/api/auth/logout',{},cookies['test'])
        self.assertEqual(self.request('development','/api/auth/me',cookie=cookies['development'])[0],200)
