"""Real HTTP reset isolation with temporary databases and synthetic accounts."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPCookieProcessor, Request

import app
import environment_tools as tools
import manage_environment as manager


class AccountResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.environment = patch.dict(os.environ, NIUMA_ENVIRONMENT='test', NIUMA_ENABLE_MANUAL_MATERIALS='1')
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.database = patch.object(app, 'DB', Path(self.tmp.name)/'niuma.sqlite3')
        self.database.start(); self.addCleanup(self.database.stop)
        app.initialize(recover=False)
        self.clients = {}
        with app.connection() as db:
            for uid in ('alice', 'bob'):
                salt = secrets.token_hex(16)
                db.execute('INSERT INTO users VALUES(?,?,?,?,?)', (uid,uid+'@example.test',uid,salt,app.password_hash('Synthetic-2026!',salt)))
                app.email_verification.mark_verified(db, uid)
                db.execute('INSERT INTO source_credentials VALUES(?,?,?)', (uid,'campus-'+uid,b'synthetic-secret'))
                db.execute('INSERT INTO workspace VALUES(?,?,?,7)', (uid,'tasks',json.dumps([dict(id=uid,title=uid,status='not_started',plans=[],subtasks=[],due=None)])))
                db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?)', (uid,'account','same-input','digest','{}'))
                db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,?,?)', (uid,'same-request','fingerprint','{}','{}',0,None))
                db.execute('INSERT INTO public_source_imports VALUES(?,?,?)', (uid,'same-event','old-task'))
                app.usage.record(db,uid,'task_create')
        self.server = app.ThreadingHTTPServer(('127.0.0.1',0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.close_server)
        self.base = 'http://127.0.0.1:'+str(self.server.server_port)
        for uid in ('alice','bob'):
            client = build_opener(HTTPCookieProcessor(CookieJar()))
            self.clients[uid] = client
            self.assertEqual(self.call(client,'/api/auth/login',dict(email=uid+'@example.test',password='Synthetic-2026!'))[0],200)

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def call(self, client, path, body=None, method=None, headers=None):
        request = Request(self.base+path, None if body is None else json.dumps(body).encode(),
                          {'Content-Type':'application/json', **(headers or {})}, method=method)
        try: response = client.open(request, timeout=10)
        except HTTPError as error: response = error
        with response:
            data = response.read()
            return response.status, json.loads(data) if 'application/json' in response.headers.get('Content-Type','') else data.decode()

    def reset(self, client=None):
        return self.call(client or self.clients['alice'], tools.RESET_URL, {'confirm':True,'accountId':'alice'})

    def test_clears_account_records_preserves_identity_connection_and_other_account(self):
        with app.connection() as db:
            identity = tuple(db.execute('SELECT * FROM users WHERE id="alice"').fetchone())
            connection = tuple(db.execute('SELECT * FROM source_credentials WHERE user_id="alice"').fetchone())
            before = db.execute('SELECT value,version FROM workspace WHERE user_id="bob"').fetchone()
        self.assertEqual(self.reset(),(200,{'reset':True,'pendingFiles':0}))
        self.assertEqual(self.call(self.clients['alice'],'/api/auth/me')[1]['user']['emailVerified'],True)
        with app.connection() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM users WHERE id="alice"').fetchone()),identity)
            self.assertEqual(tuple(db.execute('SELECT * FROM source_credentials WHERE user_id="alice"').fetchone()),connection)
            self.assertEqual(tuple(db.execute('SELECT value,version FROM workspace WHERE user_id="bob"').fetchone()),tuple(before))
            self.assertEqual(json.loads(db.execute('SELECT value FROM workspace WHERE user_id="alice" AND name="tasks"').fetchone()[0]),[])
            for table in tools.OWNER_TABLES:
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE user_id="alice"').fetchone()[0],0,table)
            for table in ('source_versions','recognition_batches','public_source_imports'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE user_id="bob"').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage_events WHERE actor=?',(app.usage.actor('alice'),)).fetchone()[0],0)
            # The exact same identities can be imported again after clearing dedup.
            db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?)', ('alice','account','same-input','digest','{}'))
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,?,?)', ('alice','same-request','fingerprint','{}','{}',0,None))
            db.execute('INSERT INTO public_source_imports VALUES(?,?,?)', ('alice','same-event','new-task'))
        self.assertEqual(self.reset()[0],200)
        self.assertEqual(self.call(self.clients['bob'],'/api/auth/me')[0],200)
        self.assertEqual(self.call(self.clients['alice'],'/api/sources/sync-schedule',dict(contract_version='source-sync/0.1',enabled=False,revision=0,interval_minutes=180,consent=False,sources=['teaching_web']),method='PUT')[0],409)

    def test_auth_confirmation_origin_and_storage_guards(self):
        anonymous=build_opener()
        self.assertEqual(self.reset(anonymous)[0],401)
        self.assertEqual(self.call(self.clients['alice'],tools.RESET_URL,{'confirm':True,'userId':'bob'})[0],400)
        self.assertEqual(self.call(self.clients['alice'],tools.RESET_URL,{'confirm':False})[0],400)
        self.assertEqual(self.call(self.clients['alice'],tools.RESET_URL,{'confirm':True,'accountId':'bob'})[0],409)
        self.assertEqual(self.call(self.clients['alice'],tools.RESET_URL,{'confirm':True,'accountId':'alice'},headers={'Origin':'https://invalid.example'})[0],403)
        marker=app.DB.parent/'.niuma-environment.json'
        for identity in ({'environment':'production'},None):
            if identity: marker.write_text(json.dumps(identity))
            else: marker.unlink()
            self.assertEqual(self.reset()[0],409)
        self.assertEqual(len(self.call(self.clients['alice'],'/api/workspace')[1]['tasks']['value']),1)

    def test_production_legacy_and_absent_mount_have_no_capability_or_tool_asset(self):
        for mode in ('production','legacy','missing'):
            with self.subTest(mode=mode):
                with patch.dict(os.environ,NIUMA_ENVIRONMENT='production' if mode=='production' else 'test'):
                    if mode=='legacy': os.environ.pop('NIUMA_ENVIRONMENT',None)
                    loader=patch.object(app,'environment_tools',return_value=None) if mode=='missing' else patch.object(tools,'reset',side_effect=AssertionError('must not load reset'))
                    with loader:
                        self.assertNotIn('accountTools',self.call(build_opener(),'/api/version')[1])
                        self.assertNotEqual(self.call(self.clients['alice'],tools.MODULE_URL)[0],200)
                        self.assertNotEqual(self.reset()[0],200)
        rules=(Path(__file__).parent/'deploy/Dockerfile.dockerignore').read_text().splitlines()
        self.assertIn('server/environment_tools',rules)
        self.assertIn('server/test_account_reset.py',rules)

    def test_sessions_versions_schedule_and_files_prevent_old_state_returning(self):
        old=build_opener(HTTPCookieProcessor(CookieJar()))
        self.call(old,'/api/auth/login',dict(email='alice@example.test',password='Synthetic-2026!'))
        assets=app.DB.parent/'wechat-assets'
        for uid in ('alice','bob'):
            folder=assets/uid; folder.mkdir(parents=True)
            file=folder/'synthetic.txt'; file.write_text(uid)
            with app.connection() as db:
                db.execute('INSERT INTO wechat_assets VALUES(?,?,?,?,?,?,?)',(uid,uid,'synthetic.txt','text/plain',5,'digest',str(file)))
                db.execute('INSERT INTO wechat_asset_lifetime VALUES(?,?)',(uid,1))
        with app.connection() as db:
            db.execute('INSERT INTO source_sync_schedules VALUES(?,1,180,?,4,100,NULL,NULL)',('alice','credential'))
            db.execute('INSERT INTO wechat_credentials VALUES(?,?,?)',('alice','token',1))
        self.assertEqual(self.reset()[0],200)
        self.assertFalse((assets/'alice/synthetic.txt').exists())
        self.assertTrue((assets/'bob/synthetic.txt').exists())
        self.assertEqual(self.call(old,'/api/workspace')[0],401)
        self.assertEqual(self.call(self.clients['alice'],'/api/data/tasks',dict(value=[],version=7),method='PUT')[0],409)
        self.assertEqual(self.call(self.clients['alice'],'/api/data/materials',dict(value=[],version=0),method='PUT')[0],409)
        with app.connection() as db:
            self.assertEqual(tuple(db.execute('SELECT enabled,revision FROM source_sync_schedules WHERE user_id="alice"').fetchone()),(0,5))
            self.assertEqual(db.execute('SELECT token_hash FROM wechat_credentials WHERE user_id="alice"').fetchone()[0],'token')

    def test_normal_reads_progress_and_stop_remain_parallel_with_a_slow_request(self):
        entered=threading.Event(); release=threading.Event()
        original=app.Handler.route
        with app.connection() as db:
            run=app.source_jobs.start(db,'alice',dict(contract_version='source-sync/0.1',sources=['teaching_web'],consent=True,operation_id='parallel-stop'),app.ApiError)
        def route(handler):
            if handler.path=='/api/workspace?slow=1':
                entered.set(); release.wait(5)
            return original(handler)
        try:
            with patch.object(app.Handler,'route',route),ThreadPoolExecutor(max_workers=2) as pool:
                slow=pool.submit(self.call,self.clients['alice'],'/api/workspace?slow=1')
                self.assertTrue(entered.wait(3))
                for path,body in (('/api/workspace',None),('/api/sources/sync-runs/'+run['run_id'],None),
                                  ('/api/sources/sync-runs/'+run['run_id']+'/stop',dict(contract_version='source-sync/0.1'))):
                    response=pool.submit(self.call,self.clients['alice'],path,body)
                    self.assertEqual(response.result(timeout=2)[0],200,path)
                    self.assertFalse(slow.done())
                release.set();self.assertEqual(slow.result(timeout=2)[0],200)
        finally: release.set()

    def test_reset_waits_for_inflight_writes_and_then_clears_their_result(self):
        entered=threading.Event(); release=threading.Event()
        original=app.Handler.route
        def route(handler):
            if handler.path=='/api/data/tasks' and handler.command=='PUT':
                entered.set(); release.wait(5)
            return original(handler)
        try:
            with patch.object(app.Handler,'route',route),ThreadPoolExecutor(max_workers=2) as pool:
                task=dict(id='late',title='旧请求任务',status='not_started',plans=[],subtasks=[],due=None)
                write=pool.submit(self.call,self.clients['alice'],'/api/data/tasks',dict(value=[task],version=7),'PUT')
                self.assertTrue(entered.wait(3))
                reset=pool.submit(self.reset)
                with self.assertRaises(TimeoutError):reset.result(timeout=.1)
                release.set()
                self.assertEqual(write.result(timeout=2)[0],200)
                self.assertEqual(reset.result(timeout=2)[0],200)
            workspace=self.call(self.clients['alice'],'/api/workspace')[1]
            self.assertEqual(workspace['tasks']['value'],[])
            self.assertEqual(workspace['tasks']['version'],9)
        finally: release.set()

    def test_exclusive_reset_rejects_queued_old_writes_but_not_other_accounts(self):
        entered=threading.Event();release=threading.Event();queued=threading.Event()
        reset_impl=tools.reset; user_impl=app.Handler.user
        # Deliberately collide with the previous fixed-bucket implementation.
        bucket=int(hashlib.sha256(b'alice').hexdigest()[:8],16)%64
        uid=next('other-'+str(i) for i in range(1000) if int(hashlib.sha256(('other-'+str(i)).encode()).hexdigest()[:8],16)%64==bucket)
        other=build_opener(HTTPCookieProcessor(CookieJar()))
        with app.connection() as db:
            salt=secrets.token_hex(16)
            db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,uid+'@example.test',uid,salt,app.password_hash('Synthetic-2026!',salt)))
        self.assertEqual(self.call(other,'/api/auth/login',dict(email=uid+'@example.test',password='Synthetic-2026!'))[0],200)
        old=build_opener(HTTPCookieProcessor(CookieJar()))
        self.call(old,'/api/auth/login',dict(email='alice@example.test',password='Synthetic-2026!'))
        def held_reset(*args,**kwargs):
            entered.set();release.wait(5)
            return reset_impl(*args,**kwargs)
        def user(handler,db):
            result=user_impl(handler,db)
            if handler.headers.get('X-Reset-Test')=='waiting-old': queued.set()
            return result
        try:
            with patch.object(tools,'reset',side_effect=held_reset),patch.object(app.Handler,'user',user),ThreadPoolExecutor(max_workers=3) as pool:
                reset=pool.submit(self.reset);self.assertTrue(entered.wait(3))
                write=pool.submit(self.call,old,'/api/data/tasks',dict(value=[],version=7),'PUT',{'X-Reset-Test':'waiting-old'})
                self.assertTrue(queued.wait(3))
                with self.assertRaises(TimeoutError):write.result(timeout=.1)
                self.assertEqual(pool.submit(self.call,other,'/api/workspace').result(timeout=2)[0],200)
                release.set();self.assertEqual(reset.result(timeout=2)[0],200)
                self.assertEqual(write.result(timeout=2)[0],401)
        finally:release.set()

    def test_failure_before_commit_rolls_back_business_records_and_session_rotation(self):
        with app.connection() as db:
            db.execute('''CREATE TRIGGER synthetic_reset_failure BEFORE DELETE ON public_source_imports
                WHEN OLD.user_id='alice' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END''')
        self.assertEqual(self.reset()[0],502)
        self.assertEqual(self.call(self.clients['alice'],'/api/auth/me')[0],200)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM source_versions WHERE user_id="alice"').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recognition_batches WHERE user_id="alice"').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM public_source_imports WHERE user_id="alice"').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT version FROM workspace WHERE user_id="alice" AND name="tasks"').fetchone()[0],7)

    def test_background_collection_cannot_restore_sources_after_reset(self):
        from types import SimpleNamespace
        from ddl_text.tests.test_teaching_pipeline import example
        jobs=app.source_jobs
        entered=threading.Event(); release=threading.Event()
        request,_=example(('explicit-complete',))
        crawler=app.DB.parent/'crawler';(crawler/'node_modules/tsx').mkdir(parents=True)
        self.assertEqual(self.call(self.clients['alice'],'/api/sources/credentials',dict(username='synthetic-course-user',password='synthetic-password'))[0],200)
        original_capture=app.source_sync.subprocess.run
        def capture(*args,**kwargs):
            if 'src/pku-homework-cli.ts' not in args[0]: return original_capture(*args,**kwargs)
            entered.set();release.wait(5)
            return SimpleNamespace(returncode=0,stdout=json.dumps(request),stderr='')
        try:
            with patch.object(app.source_sync,'CRAWLER',crawler),patch.object(app.source_sync.subprocess,'run',side_effect=capture),patch('threading.excepthook') as uncaught:
                status,run=self.call(self.clients['alice'],'/api/sources/sync-runs',dict(contract_version='source-sync/0.1',sources=['teaching_web'],consent=True,operation_id='reset-inflight'))
                self.assertEqual(status,202,run);self.assertTrue(entered.wait(3))
                self.assertEqual(self.reset()[0],200)
                release.set();jobs.wait_for_jobs(app.DB,timeout=5)
                uncaught.assert_not_called()
            with app.connection() as db:
                for table in tools.OWNER_TABLES:
                    self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE user_id="alice"').fetchone()[0],0,table)
                self.assertEqual(json.loads(db.execute('SELECT value FROM workspace WHERE user_id="alice" AND name="tasks"').fetchone()[0]),[])
        finally:
            release.set();jobs.wait_for_jobs(app.DB,timeout=5)

    def test_late_diagnostics_from_cancelled_work_do_not_return(self):
        from server import recognition_diagnostics as diagnostics
        attempt = dict(attempt_id='old',request_id='old-run',started_at='2000-01-01T00:00:00+00:00',events=[])
        self.assertEqual(self.reset()[0],200)
        with app.connection() as db:
            diagnostics.persist(db,'alice',attempt)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recognition_attempt_diagnostics WHERE user_id="alice"').fetchone()[0],0)
            attempt.update(attempt_id='new',started_at='2999-01-01T00:00:00+00:00')
            diagnostics.persist(db,'alice',attempt)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recognition_attempt_diagnostics WHERE user_id="alice"').fetchone()[0],1)

    def test_compose_mount_is_added_only_for_nonproduction(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('development','test','production'):
                config=manager.defaults(name)
                path=manager.initialize(name,Path(folder))
                command=manager.compose_command(name,path,config)
                self.assertEqual(any('compose.testing-tools.yaml' in arg for arg in command),name!='production')


if __name__=='__main__': unittest.main()
