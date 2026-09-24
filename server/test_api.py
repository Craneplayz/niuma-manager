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

    def test_courses_persist_and_reject_unsafe_links(self):
        a,b=self.client(),self.client();self.register(a,'courses@test.local');self.register(b,'courses-other@test.local')
        course=dict(id='c1',name='数学',teacher='老师',url='https://example.com/material',notes='手写笔记')
        self.assertEqual(self.call(a,'/data/courses','PUT',dict(value=[course],version=0))[0],200)
        self.assertEqual(self.call(a,'/workspace')[1]['courses']['value'],[course])
        self.assertNotIn('courses',self.call(b,'/workspace')[1])
        self.assertEqual(self.call(a,'/data/courses','PUT',dict(value=[course],version=0))[0],409)
        for url in ['javascript:alert(1)','https://user:password@example.com','https://']:
            self.assertEqual(self.call(a,'/data/courses','PUT',dict(value=[dict(course,url=url)],version=1))[0],400)
        self.assertEqual(self.call(a,'/data/courses','PUT',dict(value=[course,dict(course,id='c2')],version=1))[0],400)

    def test_focus_persistence_validation_and_owner(self):
        a,b=self.client(),self.client();self.register(a,'focus@test.local');self.register(b,'focus-other@test.local')
        timer=dict(duration=1500,remaining=1500,running=True,endsAt=app.time.time()*1000+1500000,taskId='')
        self.assertEqual(self.call(a,'/data/focus','PUT',dict(value=timer,version=0))[0],200)
        self.assertEqual(self.call(a,'/workspace')[1]['focus']['value'],timer)
        self.assertNotIn('focus',self.call(b,'/workspace')[1])
        self.assertEqual(self.call(a,'/data/focus','PUT',dict(value=timer,version=0))[0],409)
        for bad in [dict(timer,remaining=-1),dict(timer,duration=10),dict(timer,taskId='foreign'),dict(timer,endsAt=float('inf'))]:
            self.assertEqual(self.call(a,'/data/focus','PUT',dict(value=bad,version=1))[0],400)

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

    def test_recognition_demo_and_atomic_idempotent_confirmation(self):
        a,b=self.client(),self.client();self.register(a,'text-a@test.local');self.register(b,'text-b@test.local')
        status,batches=self.call(a,'/recognition/demo','POST',{})
        self.assertEqual(status,200);self.assertEqual(batches[0]['result']['status'],'partial_success')
        self.assertEqual(self.call(b,'/recognition/batches')[1],[])
        item=batches[0]['items'][1]
        body=dict(action='create',version=item['version'],tasks_version=0,reviewed=True,target_id=None,draft=dict(title='阅读材料',description='确认无期限任务',due=None))
        self.assertEqual(self.call(b,'/recognition/candidates/'+item['id']+'/resolve','POST',body)[0],404)
        self.assertEqual(self.call(a,'/recognition/candidates/'+item['id']+'/resolve','POST',dict(body,tasks_version=99))[0],409)
        self.assertEqual(self.call(a,'/recognition/candidates/'+item['id']+'/resolve','POST',body)[0],200)
        self.assertEqual(self.call(a,'/recognition/candidates/'+item['id']+'/resolve','POST',body)[0],200)
        tasks=self.call(a,'/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks),1);self.assertIsNone(tasks[0]['due']);self.assertEqual(tasks[0]['subtasks'],[])
        self.assertEqual(self.call(a,'/recognition/candidates/'+item['id']+'/resolve','POST',dict(body,action='cancel'))[0],409)
        self.assertEqual(len(self.call(a,'/recognition/demo','POST',{})[1][0]['items']),2)

    def test_recognition_transport_and_request_idempotence(self):
        a=self.client();self.register(a,'text-transport@test.local')
        fixture=json.loads((Path(__file__).parent.parent/'docs/examples/text-contract-v0.2.json').read_text())
        captured=[]
        def harness(request,timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps(fixture['expected']).encode())
        with patch.dict(app.os.environ,{'TEXT_HARNESS_URL':'http://local-test/recognize'}),patch.object(app.recognition,'urlopen',harness):
            self.assertEqual(self.call(a,'/recognition/batches','POST',fixture['request'])[0],200)
            self.assertEqual(self.call(a,'/recognition/batches','POST',fixture['request'])[0],200)
        self.assertEqual(captured,[fixture['request']])
        changed=copy.deepcopy(fixture['request']);changed['records'][0]['text']='不同内容'
        self.assertEqual(self.call(a,'/recognition/batches','POST',changed)[0],409)
        self.assertNotIn('tasks',self.call(a,'/workspace')[1])
        empty=dict(contract_version='0.2',request_id='empty',records=[])
        status,batches=self.call(a,'/recognition/batches','POST',empty)
        self.assertEqual(status,200);self.assertEqual(batches[0]['result']['status'],'no_tasks')
        fresh=copy.deepcopy(fixture['request']);fresh['request_id']='missing-service'
        with patch.dict(app.os.environ,{},clear=True):
            self.assertEqual(self.call(a,'/recognition/batches','POST',fresh)[0],503)

    def test_recognition_change_matching_and_preservation(self):
        a=self.client();self.register(a,'text-change@test.local')
        self.call(a,'/recognition/demo','POST',{})
        uid=self.call(a,'/auth/me')[1]['user']['id']
        original=dict(id='target',title='原任务',description='原要求',status='not_started',plans=[dict(id='plan',title='现有安排')],subtasks=[],due=dict(date='2026-09-25',precision='date'))
        self.assertEqual(self.call(a,'/data/tasks','PUT',dict(value=[original],version=0))[0],200)
        for n,action in enumerate(['postpone','update','cancel']):
            with app.connection() as db:
                c=json.loads(db.execute('SELECT candidate FROM recognition_candidates WHERE user_id=? LIMIT 1',(uid,)).fetchone()['candidate'])
                c['notice_type']=action
                db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',('change-'+action,uid,'demo:text-contract:0.2',json.dumps(c)))
            body=dict(action=action,version=1,tasks_version=n+1,reviewed=True,target_id='foreign',draft=dict(title='用户修订',description='用户修订要求',due=dict(date='2026-10-03',precision='date')))
            path='/recognition/candidates/change-'+action+'/resolve'
            self.assertEqual(self.call(a,path,'POST',body)[0],400)
            body['target_id']='target'
            self.assertEqual(self.call(a,path,'POST',body)[0],200)
            tasks=self.call(a,'/workspace')[1]['tasks']['value']
            self.assertEqual(len(tasks),1);self.assertEqual(tasks[0]['plans'],original['plans'])
            if action=='postpone':self.assertEqual(tasks[0]['title'],'原任务')
            if action=='cancel':self.assertEqual(tasks[0]['status'],'cancelled')

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
