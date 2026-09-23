import copy
import json
import io
import tempfile
import threading
import unittest
from pathlib import Path
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor, Request
from urllib.error import HTTPError
from unittest.mock import patch
import app

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        app.DB=Path(cls.tmp.name)/'test.sqlite3'
        app.initialize()
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)+'/api'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()

    def client(self): return build_opener(HTTPCookieProcessor(CookieJar()))
    def test_seed_is_shareable_and_idempotent(self):
        app.seed()
        with app.connection() as db:
            user=db.execute('SELECT * FROM users WHERE email=?',('demo@niuma.test',)).fetchone()
            self.assertEqual(user['hash'],app.password_hash('Niuma-Test-2026!',user['salt']))
            before=[tuple(r) for r in db.execute('SELECT * FROM workspace WHERE user_id=? ORDER BY name',(user['id'],))]
            data={r['name']:json.loads(r['value']) for r in db.execute('SELECT * FROM workspace WHERE user_id=?',(user['id'],))}
            self.assertEqual(len(data['tasks']),6)
            self.assertEqual(len(data['feedback']),3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(user['id'],)).fetchone()[0],0)
        app.seed()
        with app.connection() as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM workspace WHERE user_id=? ORDER BY name',(user['id'],))])
    def call(self,client,path,method='GET',data=None):
        try:
            response=client.open(Request(self.base+path,None if data is None else json.dumps(data).encode(),{'Content-Type':'application/json'},method=method))
        except HTTPError as e: response=e
        return response.status,json.loads(response.read())
    def register(self,client,email):
        status,_=self.call(client,'/auth/register','POST',dict(name='Test',email=email,password='Test-password-2026'))
        self.assertEqual(status,200)

    def test_accounts_persistence_isolation_versions_logout(self):
        a,b=self.client(),self.client()
        self.assertEqual(self.call(a,'/workspace')[0],401)
        self.register(a,'a@test.local');self.register(b,'b@test.local')
        task=dict(id='task',title='报告',status='not_started',plans=[],subtasks=[],due=None)
        self.assertEqual(self.call(a,'/data/tasks','PUT',dict(value=[task],version=0))[0],200)
        self.assertEqual(self.call(a,'/data/tasks','PUT',dict(value=[],version=0))[0],409)
        self.assertNotIn('tasks',self.call(b,'/workspace')[1])
        with app.connection() as db:
            self.assertEqual(json.loads(db.execute('SELECT value FROM workspace WHERE name="tasks"').fetchone()['value'])[0]['title'],'报告')
            self.assertNotEqual(db.execute('SELECT hash FROM users LIMIT 1').fetchone()['hash'],'Test-password-2026')
        self.assertEqual(self.call(a,'/auth/logout','POST',{})[0],200)
        self.assertEqual(self.call(a,'/workspace')[0],401)
        self.assertEqual(self.call(a,'/auth/login','POST',dict(email='a@test.local',password='wrong-password'))[0],401)
        self.assertEqual(self.call(a,'/auth/login','POST',dict(email='a@test.local',password='Test-password-2026'))[0],200)
        self.assertEqual(len(self.call(a,'/workspace')[1]['tasks']['value']),1)

    def test_feedback_and_no_model(self):
        a=self.client();self.register(a,'feedback@test.local')
        self.assertEqual(self.call(a,'/data/feedback','PUT',dict(value=[dict(taskId='foreign',actualMinutes=10)],version=0))[0],400)
        with patch.dict(app.os.environ,{},clear=True):
            self.assertEqual(self.call(a,'/planning/proposals','POST',{})[0],503)
        self.assertEqual(self.call(a,'/data/availability','PUT',dict(value=dict(startTime='22:00',endTime='19:00',weekdays=[]),version=0))[0],400)

    def test_harness_snapshot_exclusions_feedback_and_atomic_apply(self):
        a=self.client();self.register(a,'planner@test.local')
        day=(app.datetime.now(app.TZ)+app.timedelta(days=1)).date().isoformat()
        base=dict(title='报告',description='测试',status='not_started',plans=[],subtasks=[],due=dict(date=day,precision='date'))
        tasks=[dict(base,id='plan'),dict(base,id='excluded',planningExcluded=True),dict(base,id='done',status='completed')]
        self.assertEqual(self.call(a,'/data/tasks','PUT',dict(value=tasks,version=0))[0],200)
        self.assertEqual(self.call(a,'/data/feedback','PUT',dict(value=[dict(taskId='done',actualMinutes=80,recordedAt='2026-09-23T00:00:00Z')],version=0))[0],200)
        self.assertEqual(self.call(a,'/data/availability','PUT',dict(value=dict(startTime='19:00',endTime='22:00',weekdays=list(range(7))),version=0))[0],200)
        captured=[]
        def harness(request,timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps(dict(estimates={'plan':60},blocks=[dict(taskId='plan',title='写报告',startAt=day+'T19:00:00+08:00',endAt=day+'T20:00:00+08:00')],warnings=[])).encode())
        with patch.dict(app.os.environ,{'HARNESS_URL':'http://test-harness/planning'}),patch.object(app,'urlopen',harness):
            status,result=self.call(a,'/planning/proposals','POST',{})
        self.assertEqual(status,200)
        self.assertEqual([t['id'] for t in captured[0]['tasks']],['plan'])
        self.assertNotIn('startDate',captured[0]['availability'])
        self.assertNotIn('estimatedMinutes',captured[0]['tasks'][0])
        self.assertEqual(captured[0]['completionHistory'][0]['task']['title'],'报告')
        self.assertEqual(self.call(a,'/workspace')[1]['tasks']['value'][0]['plans'],[])
        status,applied=self.call(a,'/planning/apply','POST',dict(requestId=result['requestId']))
        self.assertEqual(status,200);self.assertEqual(len(applied['value'][0]['plans']),1)
        self.assertEqual(applied['value'][0]['due'],base['due'])
        self.assertEqual(self.call(a,'/planning/apply','POST',dict(requestId=result['requestId']))[0],409)

class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.request=dict(requestedAt='2026-09-23T10:00:00+08:00',tasks=[dict(id='a',title='报告',due=dict(date='2026-09-24',precision='date'))],existingPlans=[],availability=dict(startTime='19:00',endTime='22:00',weekdays=[0,1,2,3,4,5,6]))
        self.result=dict(estimates={'a':60},blocks=[dict(taskId='a',title='写报告',startAt='2026-09-23T19:00:00+08:00',endAt='2026-09-23T20:00:00+08:00')],warnings=[])
    def test_valid_and_ddl_unchanged(self):
        before=copy.deepcopy(self.request);app.validate_proposal(self.request,self.result);self.assertEqual(before,self.request)
    def test_conflict_rejected(self):
        self.request['existingPlans']=[self.result['blocks'][0]]
        with self.assertRaises(app.ApiError): app.validate_proposal(self.request,self.result)
    def test_unknown_task_rejected(self):
        self.result['blocks'][0]['taskId']='foreign'
        with self.assertRaises(app.ApiError): app.validate_proposal(self.request,self.result)
    def test_deadline_rejected(self):
        self.request['tasks'][0]['due']=dict(date='2026-09-23',time='19:30',precision='datetime')
        with self.assertRaises(app.ApiError): app.validate_proposal(self.request,self.result)
    def test_estimate_required(self):
        self.result['estimates']={}
        with self.assertRaises(app.ApiError): app.validate_proposal(self.request,self.result)
    def test_unscheduled_warning(self):
        self.result['estimates']['a']=120;app.validate_proposal(self.request,self.result);self.assertTrue(self.result['warnings'])

if __name__=='__main__': unittest.main()
