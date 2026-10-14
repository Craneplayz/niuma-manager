import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
from unittest.mock import patch
import app
import usage

class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=app.DB;app.DB=Path(self.temp.name)/'db.sqlite3'
        self.env=patch.dict(os.environ,NIUMA_ENVIRONMENT='test',NIUMA_ADMIN_USER_IDS='',NIUMA_COOKIE_SECURE='0');self.env.start()
        app.initialize(recover=False)
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        self.uid=self.call('/auth/register',dict(name='Synthetic',email='usage@example.test',password='Usage-Test-2026!'))[1]['user']['id']
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();app.DB=self.old;self.env.stop();self.temp.cleanup()
    def call(self,path,body=None,method=None):
        try:r=self.client.open(Request(f'http://127.0.0.1:{self.server.server_port}/api'+path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json'},method=method))
        except HTTPError as e:r=e
        return r.status,json.load(r)
    def test_admin_denied_by_default_and_not_granted_by_profile(self):
        self.assertEqual(self.call('/admin/usage')[0],401)
        self.call('/auth/profile',dict(name='Admin',admin=True))
        self.assertEqual(self.call('/admin/auth/login',dict(email='usage@example.test',password='Usage-Test-2026!'))[0],403)
        with patch.dict(os.environ,NIUMA_ADMIN_USER_IDS=self.uid):
            self.assertEqual(self.call('/admin/auth/login',dict(email='usage@example.test',password='Usage-Test-2026!'))[0],200)
            self.assertEqual(self.call('/admin/usage')[0],200)
            self.assertEqual(self.call('/admin/usage?start=2020-01-01&end=2026-01-01')[0],400)
        self.call('/auth/logout',{})
        self.call('/admin/auth/logout',{})
        self.assertEqual(self.call('/admin/usage')[0],401)
    def test_next_day_only_once_refresh_account_isolation(self):
        day=datetime(2026,10,14,12,tzinfo=usage.TZ)
        with app.connection() as db:
            self.assertFalse(usage.visit(db,self.uid,day)['offerRating']);db.commit()
            self.assertFalse(usage.visit(db,self.uid,day)['offerRating']);db.commit()
            self.assertTrue(usage.visit(db,self.uid,day+timedelta(days=1))['offerRating']);db.commit()
            self.assertFalse(usage.visit(db,self.uid,day+timedelta(days=1))['offerRating']);db.commit()
            self.assertFalse(usage.visit(db,'other',day+timedelta(days=1))['offerRating']);db.commit()
            self.assertFalse(usage.visit(db,self.uid,day+timedelta(days=2))['offerRating'])
    def test_gap_does_not_prompt_until_consecutive_use(self):
        day=datetime.now(usage.TZ)
        with app.connection() as db:
            usage.visit(db,self.uid,day);db.commit()
            self.assertFalse(usage.visit(db,self.uid,day+timedelta(days=3))['offerRating']);db.commit()
            self.assertTrue(usage.visit(db,self.uid,day+timedelta(days=4))['offerRating'])
    def test_feedback_rating_validation_and_duplicate(self):
        for score in (True,0,6,'5'):
            self.assertEqual(self.call('/usage/feedback',dict(kind='rating',score=score))[0],400)
        self.assertEqual(self.call('/usage/feedback',dict(kind='feedback',message=''))[0],400)
        for _ in range(2):self.assertEqual(self.call('/usage/feedback',dict(kind='rating',score=4))[0],200)
        self.assertEqual(self.call('/usage/feedback',dict(kind='feedback',message='希望更简洁'))[0],200)
        with app.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM usage_feedback').fetchone()[0],2)
    def test_task_counts_only_committed_changes_and_no_content(self):
        task=dict(id='secret-task-id',title='PRIVATE-TITLE',description='PRIVATE-CONTENT',status='not_started',plans=[],subtasks=[],due=None)
        self.assertEqual(self.call('/data/tasks',dict(value=[task],version=0),'PUT')[0],200)
        self.assertEqual(self.call('/data/tasks',dict(value=[task],version=0),'PUT')[0],409)
        task['status']='completed'
        self.assertEqual(self.call('/data/tasks',dict(value=[task],version=1),'PUT')[0],200)
        self.assertEqual(self.call('/data/tasks',dict(value=[task],version=2),'PUT')[0],200)
        with app.connection() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM usage_events')]
            self.assertEqual(sum(r['quantity'] for r in rows if r['event']=='task_created'),1)
            self.assertEqual(sum(r['quantity'] for r in rows if r['event']=='task_completed'),1)
            encoded=json.dumps(rows);self.assertNotIn('PRIVATE',encoded);self.assertNotIn('secret-task-id',encoded);self.assertNotIn('usage@example.test',encoded)
    def test_retention_and_excluded_users(self):
        now=datetime.now(usage.TZ)
        with app.connection() as db:
            usage.record(db,'other','sync',now=now-timedelta(days=100))
            usage.record(db,self.uid,'sync',now=now)
            self.assertFalse(db.execute('SELECT 1 FROM usage_events WHERE day<?',((now-timedelta(days=90)).date().isoformat(),)).fetchone())
            with patch.dict(os.environ,NIUMA_ANALYTICS_EXCLUDED_USER_IDS=self.uid):
                result=usage.summary(db,now.date().isoformat(),now.date().isoformat())
                self.assertEqual(result['activeUsers'],0)
    def test_persistent_admin_role_and_business_failure_status(self):
        with app.connection() as db:
            db.execute('INSERT INTO usage_admins VALUES(?)',(usage.actor(self.uid),))
        self.assertEqual(self.call('/admin/auth/login',dict(email='usage@example.test',password='Usage-Test-2026!'))[0],200)
        self.assertEqual(self.call('/admin/usage')[0],200)
        with app.connection() as db:
            db.execute('DELETE FROM usage_admins')
        self.assertEqual(self.call('/admin/usage')[0],403)
        value=[dict(request=dict(request_id='old'),result=dict(status='failed')),dict(request=dict(request_id='new'),result=dict(status='success'))]
        self.assertTrue(usage.successful(200,value,'new'))
        self.assertFalse(usage.successful(200,value,'old'))
        self.assertFalse(usage.successful(502,{}))
    def test_admin_login_does_not_switch_or_log_out_ordinary_session(self):
        salt='0123456789abcdef0123456789abcdef'
        with app.connection() as db:
            db.execute('INSERT INTO users VALUES(?,?,?,?,?)',('other-admin','other-admin@example.test','Admin',salt,app.password_hash('Other-Admin-2026!',salt)))
            db.execute('INSERT INTO usage_admins VALUES(?)',(usage.actor('other-admin'),))
        self.assertEqual(self.call('/admin/auth/login',dict(email='other-admin@example.test',password='Other-Admin-2026!'))[0],200)
        self.assertEqual(self.call('/auth/me')[1]['user']['id'],self.uid)
        self.call('/admin/auth/logout',{})
        self.assertEqual(self.call('/auth/me')[1]['user']['id'],self.uid)
        self.assertEqual(self.call('/admin/usage')[0],401)
    def test_invalid_legacy_due_does_not_make_analytics_block_task_save(self):
        with app.connection() as db:
            usage.workspace_changes(db,self.uid,[],[dict(id='legacy',status='completed',due=dict(date='unknown'))])
            self.assertFalse(db.execute('SELECT 1 FROM usage_events WHERE event="dated_task_completed"').fetchone())
    def test_median_and_nearest_rank_p95_boundaries(self):
        with app.connection() as db:
            for samples,expected_median,expected_p95 in [([100],100,100),([100,1100],600,1100),(list(range(1,21)),10.5,19),(list(range(1,22)),11,20)]:
                with self.subTest(samples=len(samples)):
                    db.execute('DELETE FROM usage_events')
                    for duration in samples:usage.record(db,self.uid,'planning',duration_ms=duration)
                    result=usage.summary(db,'2000-01-01','2099-01-01')['metrics'][0]
                    self.assertEqual(result['medianMs'],expected_median)
                    self.assertEqual(result['p95Ms'],expected_p95)
            db.execute('DELETE FROM usage_events')
            self.assertEqual(usage.summary(db,'2000-01-01','2099-01-01')['metrics'],[])
    def test_future_deadline_completion_has_numeric_only_on_time_metric(self):
        task=dict(id='a',status='completed',due=dict(date='2099-01-01',time='10:00'))
        with app.connection() as db:
            usage.workspace_changes(db,self.uid,[],[task])
            result=usage.summary(db,'2000-01-01','2099-01-01')
            values={m['event']:m['quantity'] for m in result['metrics']}
            self.assertEqual(values['dated_task_completed'],1);self.assertEqual(values['task_on_time'],1)
    def test_long_planning_request_does_not_claim_other_window_task_changes(self):
        task=dict(id='a',title='Synthetic',status='not_started',plans=[],subtasks=[],due=dict(date='2099-01-01',precision='date'))
        self.assertEqual(self.call('/data/tasks',dict(value=[task],version=0),'PUT')[0],200)
        self.assertEqual(self.call('/data/availability',dict(value=dict(startTime='09:00',endTime='23:00',weekdays=list(range(7))),version=0),'PUT')[0],200)
        def model(*args):
            task['status']='completed'
            with app.connection() as db:db.execute('UPDATE workspace SET value=?,version=version+1 WHERE user_id=? AND name="tasks"',(json.dumps([task]),self.uid))
            return dict(blocks=[],warnings=[],estimates={'a':15})
        with patch.object(app.planning,'plan',side_effect=model):
            self.assertEqual(self.call('/planning/proposals',{})[0],200)
        with app.connection() as db:
            self.assertFalse(db.execute('SELECT 1 FROM usage_events WHERE event="task_completed"').fetchone())
