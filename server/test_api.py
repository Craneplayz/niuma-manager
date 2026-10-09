import copy
import json
import io
import tempfile
import threading
import unittest
import shutil
import subprocess
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
    def test_connection_closes_on_success_and_failure(self):
        import sqlite3
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT 1').fetchone()[0],1)
        with self.assertRaises(sqlite3.ProgrammingError): db.execute('SELECT 1')
        with self.assertRaises(RuntimeError):
            with app.connection() as failed:
                raise RuntimeError('synthetic transaction failure')
        with self.assertRaises(sqlite3.ProgrammingError): failed.execute('SELECT 1')
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

    def test_calendar_editor_store_http_save_preserves_released_completed_history(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node.js is required for the frontend HTTP integration')
        client=self.client();self.register(client,'manual-calendar-http@test.local')
        date='2099-10-08'
        completed_plan=dict(id='released',title='已完成安排',startAt=date+'T19:00:00+08:00',endAt=date+'T20:00:00+08:00',origin='ai',isLocked=True)
        active_plan=dict(id='active-plan',title='学习',startAt=date+'T21:00:00+08:00',endAt=date+'T22:00:00+08:00',origin='ai')
        tasks=[dict(id='done',title='已完成任务',status='completed',plans=[completed_plan],subtasks=[],due=None),
               dict(id='active',title='未完成任务',status='not_started',plans=[active_plan],subtasks=[],due=None)]
        self.assertEqual(self.call(client,'/data/tasks','PUT',dict(value=tasks,version=0))[0],200)
        jar=next(handler.cookiejar for handler in client.handlers if isinstance(handler,HTTPCookieProcessor))
        environment=dict(app.os.environ,NIUMA_TEST_API=self.base.rsplit('/api',1)[0],
                         NIUMA_TEST_COOKIE='; '.join(cookie.name+'='+cookie.value for cookie in jar))
        script='''
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();globalThis.document=new EventTarget();
globalThis.requestAnimationFrame=()=>0;
globalThis.FormData=class {constructor(form){return new Map(Object.entries(form.values));}};
Date.now=()=>Date.parse('2099-10-08T12:00:00+08:00');
const realFetch=fetch;
globalThis.fetch=(path,options={})=>realFetch(process.env.NIUMA_TEST_API+path,
 {...options,headers:{...options.headers,Cookie:process.env.NIUMA_TEST_COOKIE}});
const root=process.argv[1];
const {initSession,saveStore,api}=await import(root+'/store.js');
const {calendarPage,calendarClick}=await import(root+'/team-calendar-ui.js');
const {configureScheduleEditor,scheduleEditorSubmit}=await import(root+'/schedule-editor.js');
const state=await initSession();
calendarClick({dataset:{calendarDay:'2099-10-08'}},()=>{});
assert.doesNotMatch(calendarPage(state,0),/data-schedule-id="released"/);
const error={textContent:''},form={id:'schedule-form',dataset:{planId:'active-plan',taskId:'active'},
 values:{date:'2099-10-08',start:'19:00',endDate:'2099-10-08',end:'20:00',label:'学习'},
 querySelector:()=>error,querySelectorAll:()=>[]};
let finish,timer;const finished=new Promise(resolve=>{finish=resolve;});
configureScheduleEditor({getState:()=>state,persist:()=>saveStore(state),closeDialog:()=>{},render:()=>{},toast:finish});
assert.equal(scheduleEditorSubmit(form),true);
try{assert.equal(await Promise.race([finished,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(error.textContent||'editor save timed out')),10000);})]),'学习时段已保存');}
finally{clearTimeout(timer);}
assert.equal(error.textContent,'');
const saved=await api('/api/workspace');
assert.equal(saved.tasks.version,2);
assert.equal(saved.tasks.value.find(task=>task.id==='active').plans[0].startAt,'2099-10-08T19:00:00+08:00');
'''
        result=subprocess.run([node,'--input-type=module','-e',script,
            (Path(__file__).resolve().parent.parent/'frontend/src').as_uri()],
            env=environment,capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        stored=self.call(client,'/workspace')[1]['tasks']
        self.assertEqual(stored['version'],2)
        by_id={task['id']:task for task in stored['value']}
        self.assertEqual(by_id['done']['plans'],[completed_plan])
        self.assertEqual(by_id['done']['status'],'completed')
        self.assertEqual(by_id['active']['plans'][0]['id'],'active-plan')
        self.assertEqual(by_id['active']['plans'][0]['origin'],'manual')

    def test_task_delete_permanently_removes_task_plans_feedback_and_dependencies(self):
        client=self.client();self.register(client,'delete@test.local')
        removed=dict(id='removed',title='删除我',status='completed',completedAt='2026-10-06',dependsOn='',plans=[dict(id='plan',title='日程',startAt='2026-10-07T10:00:00+08:00',endAt='2026-10-07T11:00:00+08:00')],subtasks=[],due=None)
        kept=dict(id='kept',title='保留我',status='not_started',dependsOn='removed',plans=[],subtasks=[],due=None)
        self.assertEqual(self.call(client,'/data/tasks','PUT',dict(value=[removed,kept],version=0))[0],200)
        self.assertEqual(self.call(client,'/data/feedback','PUT',dict(value=[dict(taskId='removed',actualMinutes=30)],version=0))[0],200)
        status,result=self.call(client,'/data/tasks','DELETE',dict(ids=['removed'],version=1))
        self.assertEqual(status,200);self.assertEqual(result['deletedIds'],['removed'])
        workspace=self.call(client,'/workspace')[1]
        self.assertEqual([task['id'] for task in workspace['tasks']['value']],['kept'])
        self.assertEqual(workspace['tasks']['value'][0]['dependsOn'],'')
        self.assertEqual(workspace['feedback']['value'],[])
        self.assertEqual(self.call(client,'/data/tasks','DELETE',dict(ids=['removed'],version=2))[0],404)

    def test_account_profile_and_password_management(self):
        a,b=self.client(),self.client();email='account@test.local'
        self.register(a,email)
        self.assertEqual(self.call(b,'/auth/login','POST',dict(email=email,password='Test-password-2026'))[0],200)
        for name in ['', 'x'*41]: self.assertEqual(self.call(a,'/auth/profile','POST',dict(name=name))[0],400)
        status,result=self.call(a,'/auth/profile','POST',dict(name='新的昵称'))
        self.assertEqual(status,200);self.assertEqual(result['user']['name'],'新的昵称')
        self.assertEqual(self.call(b,'/auth/me')[1]['user']['name'],'新的昵称')
        self.assertEqual(self.call(a,'/auth/password','POST',dict(currentPassword='wrong-password',newPassword='New-password-2026'))[0],400)
        self.assertEqual(self.call(a,'/auth/password','POST',dict(currentPassword='Test-password-2026',newPassword='short'))[0],400)
        self.assertEqual(self.call(a,'/auth/password','POST',dict(currentPassword='Test-password-2026',newPassword='New-password-2026'))[0],200)
        self.assertEqual(self.call(a,'/auth/me')[0],200)
        self.assertEqual(self.call(b,'/auth/me')[0],401)
        old=self.client();self.assertEqual(self.call(old,'/auth/login','POST',dict(email=email,password='Test-password-2026'))[0],401)
        fresh=self.client();self.assertEqual(self.call(fresh,'/auth/login','POST',dict(email=email,password='New-password-2026'))[0],200)

    def test_feedback_and_no_model(self):
        a=self.client();self.register(a,'feedback@test.local')
        self.assertEqual(self.call(a,'/data/preferences','PUT',dict(value=dict(askCompletionMinutes=True),version=0))[0],200)
        self.assertTrue(self.call(a,'/workspace')[1]['preferences']['value']['askCompletionMinutes'])
        for value in [{},dict(askCompletionMinutes='yes'),dict(askCompletionMinutes=True,extra=True)]:
            self.assertEqual(self.call(a,'/data/preferences','PUT',dict(value=value,version=1))[0],400)
        self.assertEqual(self.call(a,'/data/feedback','PUT',dict(value=[dict(taskId='foreign',actualMinutes=10)],version=0))[0],400)
        with patch.object(app.planning,'plan') as model:
            self.assertEqual(self.call(a,'/planning/proposals','POST',{})[0],400)
            model.assert_not_called()
        self.assertEqual(self.call(a,'/data/availability','PUT',dict(value=dict(startTime='22:00',endTime='19:00',weekdays=[]),version=0))[0],400)

    def test_deadline_time_edit_preserves_plans(self):
        client=self.client();self.register(client,'deadline-edit@test.local')
        plan=dict(id='p1',title='原执行安排',startAt='2026-10-01T10:00:00+08:00',endAt='2026-10-01T11:00:00+08:00',origin='manual')
        task=dict(id='ddl-edit',title='截止时间测试',status='not_started',subtasks=[],plans=[plan],due=None)
        deadlines=[dict(date='2026-10-03',precision='date'),dict(date='2026-10-03',time='18:30',precision='datetime'),dict(date='2026-10-03',time='00:00',precision='datetime'),dict(date='2026-10-03',precision='date'),None]
        for version,due in enumerate(deadlines):
            task['due']=due
            self.assertEqual(self.call(client,'/data/tasks','PUT',dict(value=[task],version=version))[0],200)
            stored=self.call(client,'/workspace')[1]['tasks']['value'][0]
            self.assertEqual(stored['due'],due)
            self.assertEqual(stored['plans'],[plan])

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

    def test_task_completion_invalidates_an_unapplied_planning_proposal(self):
        client=self.client();self.register(client,'completion-stale-plan@test.local')
        # This tests task versions, not the default Monday-Friday availability.
        self.assertEqual(self.call(client,'/data/availability','PUT',dict(value=dict(startTime='19:00',endTime='23:00',weekdays=list(range(7))),version=0))[0],200)
        day=(app.datetime.now(app.TZ)+app.timedelta(days=1)).date().isoformat()
        task=dict(id='finished',title='报告',status='not_started',plans=[],subtasks=[],due=dict(date=day,precision='date'))
        self.assertEqual(self.call(client,'/data/tasks','PUT',dict(value=[task],version=0))[0],200)
        result=dict(estimates={'finished':60},blocks=[dict(taskId='finished',title='写报告',startAt=day+'T19:00:00+08:00',endAt=day+'T20:00:00+08:00')],warnings=[])
        with patch.object(app.planning,'plan',return_value=result):
            status,proposal=self.call(client,'/planning/proposals','POST',{})
        self.assertEqual(status,200)
        task['status']='completed'
        self.assertEqual(self.call(client,'/data/tasks','PUT',dict(value=[task],version=1))[0],200)
        self.assertEqual(self.call(client,'/planning/apply','POST',dict(requestId=proposal['requestId']))[0],409)
        self.assertEqual(self.call(client,'/workspace')[1]['tasks']['value'],[task])
        with patch.object(app.planning,'plan') as model:
            self.assertEqual(self.call(client,'/planning/proposals','POST',{})[0],400)
            model.assert_not_called()

    def test_harness_snapshot_exclusions_feedback_and_atomic_apply(self):
        a=self.client();self.register(a,'planner@test.local')
        day=(app.datetime.now(app.TZ)+app.timedelta(days=1)).date().isoformat()
        base=dict(title='报告',description='测试',status='not_started',plans=[],subtasks=[],due=dict(date=day,precision='date'))
        now=app.datetime.now(app.TZ)
        completed_plans=[dict(id='done-future',title='已完成任务的未来安排',startAt=day+'T19:00:00+08:00',endAt=day+'T20:00:00+08:00'),
                         dict(id='done-started',title='已经开始的安排',startAt=(now-app.timedelta(minutes=15)).isoformat(),endAt=(now+app.timedelta(minutes=45)).isoformat()),
                         dict(id='done-past',title='历史安排',startAt=(now-app.timedelta(days=1)).isoformat(),endAt=(now-app.timedelta(days=1,hours=-1)).isoformat())]
        tasks=[dict(base,id='plan'),dict(base,id='excluded',planningExcluded=True),dict(base,id='done',status='completed',plans=completed_plans)]
        self.assertEqual(self.call(a,'/data/tasks','PUT',dict(value=tasks,version=0))[0],200)
        self.assertEqual(self.call(a,'/data/feedback','PUT',dict(value=[dict(taskId='done',actualMinutes=80,recordedAt='2026-09-23T00:00:00Z')],version=0))[0],200)
        self.assertEqual(self.call(a,'/data/availability','PUT',dict(value=dict(startTime='19:00',endTime='22:00',weekdays=list(range(7))),version=0))[0],200)
        captured=[]
        def harness(request,timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps(dict(estimates={'plan':60},blocks=[dict(taskId='plan',title='写报告',startAt=day+'T19:00:00+08:00',endAt=day+'T20:00:00+08:00')],warnings=[])).encode())
        with patch.dict(app.os.environ,{'HARNESS_URL':'http://test-harness/planning'}),patch.object(app.planning,'urlopen',harness):
            status,result=self.call(a,'/planning/proposals','POST',{})
        self.assertEqual(status,200)
        self.assertEqual([t['id'] for t in captured[0]['tasks']],['plan'])
        self.assertEqual([p['id'] for p in captured[0]['existingPlans']],['done-started','done-past'])
        self.assertEqual(captured[0]['completionHistory'][0]['task']['plans'],completed_plans)
        self.assertNotIn('startDate',captured[0]['availability'])
        self.assertNotIn('estimatedMinutes',captured[0]['tasks'][0])
        self.assertEqual(captured[0]['completionHistory'][0]['task']['title'],'报告')
        self.assertEqual(self.call(a,'/workspace')[1]['tasks']['value'][0]['plans'],[])
        self.assertEqual(self.call(a,'/workspace')[1]['tasks']['value'][2]['plans'],completed_plans)
        status,applied=self.call(a,'/planning/apply','POST',dict(requestId=result['requestId']))
        self.assertEqual(status,200);self.assertEqual(len(applied['value'][0]['plans']),1)
        self.assertEqual(applied['value'][0]['due'],base['due'])
        self.assertEqual(self.call(a,'/planning/apply','POST',dict(requestId=result['requestId']))[0],409)

    def test_auto_proposal_only_targets_new_dated_tasks_and_never_applies_itself(self):
        a=self.client();self.register(a,'auto-plan@test.local')
        day=(app.datetime.now(app.TZ)+app.timedelta(days=2)).date().isoformat()
        base=dict(title='作业',description='测试',status='not_started',plans=[],subtasks=[])
        tasks=[dict(base,id='dated',due=dict(date=day,precision='date')),
               dict(base,id='undated',due=None),dict(base,id='other',due=dict(date=day,precision='date'))]
        self.assertEqual(self.call(a,'/data/tasks','PUT',dict(value=tasks,version=0))[0],200)
        for ids in [[],['dated','dated'],'dated',['missing'],['undated']]:
            self.assertEqual(self.call(a,'/planning/proposals','POST',dict(taskIds=ids))[0],400)
        captured=[]
        def plan(payload,ApiError):
            captured.append(payload)
            return dict(estimates={'dated':60},blocks=[],warnings=[])
        with patch.object(app.planning,'plan',plan):
            status,result=self.call(a,'/planning/proposals','POST',dict(taskIds=['dated','undated']))
        self.assertEqual(status,200)
        self.assertEqual([t['id'] for t in captured[0]['tasks']],['dated'])
        self.assertEqual(result['blocks'],[])
        self.assertTrue(all(not t['plans'] for t in self.call(a,'/workspace')[1]['tasks']['value']))

    def test_replanning_only_replaces_selected_future_blocks_after_confirmation(self):
        client=self.client();self.register(client,'replan@test.local')
        now=app.datetime.now(app.TZ);day=(now+app.timedelta(days=1)).date().isoformat()
        past=dict(id='past',title='过去的学习',startAt=(now-app.timedelta(days=2)).isoformat(),endAt=(now-app.timedelta(days=2,hours=-1)).isoformat(),origin='manual')
        ongoing=dict(id='ongoing',title='正在学习',startAt=(now-app.timedelta(minutes=10)).isoformat(),endAt=(now+app.timedelta(minutes=10)).isoformat(),origin='manual')
        def plan(pid,hour): return dict(id=pid,title='原安排',startAt=f'{day}T{hour}:00:00+08:00',endAt=f'{day}T{hour+1}:00:00+08:00',origin='ai')
        base=dict(title='报告',status='not_started',due=dict(date=day,precision='date'),subtasks=[],priority='medium')
        tasks=[dict(base,id='a',plans=[past,ongoing,plan('replace',19)]),dict(base,id='b',plans=[plan('no-block',20)]),dict(base,id='c',plans=[plan('untouched',21)])]
        self.assertEqual(self.call(client,'/data/tasks','PUT',dict(value=tasks,version=0))[0],200)
        self.assertEqual(self.call(client,'/data/availability','PUT',dict(value=dict(startTime='19:00',endTime='23:00',weekdays=list(range(7))),version=0))[0],200)
        captured=[]
        def model(payload,ApiError):
            captured.append(copy.deepcopy(payload))
            return dict(estimates={'a':60,'b':60},blocks=[dict(taskId='a',title='新安排',startAt=day+'T22:00:00+08:00',endAt=day+'T23:00:00+08:00')],warnings=[])
        with patch.object(app.planning,'plan',model):
            status,proposal=self.call(client,'/planning/proposals','POST',json.loads((Path(__file__).resolve().parent.parent / 'contracts/task-planning/api-examples.json').read_text(encoding='utf-8'))['scopedReplanRequest'])
        self.assertEqual(status,200);self.assertEqual(proposal['replannedTaskIds'],['a'])
        self.assertEqual([task['id'] for task in captured[0]['tasks']],['a','b'])
        self.assertEqual({plan['id'] for plan in captured[0]['existingPlans']},{'past','ongoing','untouched'})
        self.assertEqual(self.call(client,'/workspace')[1]['tasks']['value'],tasks)
        status,result=self.call(client,'/planning/apply','POST',dict(requestId=proposal['requestId']))
        self.assertEqual(status,200)
        changed=result['value'][0]
        self.assertEqual([plan['id'] for plan in changed['plans'][:2]],['past','ongoing'])
        self.assertEqual(changed['plans'][2]['title'],'新安排')
        self.assertEqual(changed['due'],base['due'])
        self.assertEqual(result['value'][1:],tasks[1:])
        self.assertEqual(self.call(client,'/workspace')[1]['tasks']['value'],result['value'])
        self.assertEqual(self.call(client,'/planning/apply','POST',dict(requestId=proposal['requestId']))[0],409)

    def test_replanning_failure_empty_preview_and_stale_apply_preserve_old_schedule(self):
        client=self.client();self.register(client,'replan-failure@test.local')
        self.assertEqual(self.call(client,'/data/availability','PUT',dict(value=dict(startTime='19:00',endTime='23:00',weekdays=list(range(7))),version=0))[0],200)
        day=(app.datetime.now(app.TZ)+app.timedelta(days=1)).date().isoformat()
        task=dict(id='a',title='报告',status='not_started',subtasks=[],due=dict(date=day,precision='date'),plans=[dict(id='old',title='原安排',startAt=day+'T19:00:00+08:00',endAt=day+'T20:00:00+08:00')])
        self.assertEqual(self.call(client,'/data/tasks','PUT',dict(value=[task],version=0))[0],200)
        for body in [dict(replan='yes'),dict(replan=True,taskIds=['foreign'])]:
            with patch.object(app.planning,'plan') as model:
                self.assertEqual(self.call(client,'/planning/proposals','POST',body)[0],400);model.assert_not_called()
        with patch.object(app.planning,'plan',side_effect=app.ApiError(502,'模型不可用')):
            self.assertEqual(self.call(client,'/planning/proposals','POST',dict(taskIds=['a'],replan=True))[0],502)
        with patch.object(app.planning,'plan',return_value=dict(estimates={'a':60},blocks=[],warnings=[])):
            status,empty=self.call(client,'/planning/proposals','POST',dict(taskIds=['a'],replan=True))
        self.assertEqual(status,200);self.assertEqual(empty['replannedTaskIds'],[])
        self.assertEqual(self.call(client,'/planning/apply','POST',dict(requestId=empty['requestId']))[0],200)
        self.assertEqual(self.call(client,'/workspace')[1]['tasks']['value'],[task])
        with patch.object(app.planning,'plan',return_value=dict(estimates={'a':60},blocks=[dict(taskId='a',title='新安排',startAt=day+'T20:00:00+08:00',endAt=day+'T21:00:00+08:00')],warnings=[])):
            status,proposal=self.call(client,'/planning/proposals','POST',dict(taskIds=['a'],replan=True))
        self.assertEqual(status,200)
        with patch.object(app,'datetime',wraps=app.datetime) as clock:
            clock.now.return_value=app.datetime.fromisoformat(day+'T19:30:00+08:00')
            self.assertEqual(self.call(client,'/planning/apply','POST',dict(requestId=proposal['requestId']))[0],409)
        self.assertEqual(self.call(client,'/workspace')[1]['tasks']['value'],[task])
        task['title']='已编辑的任务'
        self.assertEqual(self.call(client,'/data/tasks','PUT',dict(value=[task],version=2))[0],200)
        self.assertEqual(self.call(client,'/planning/apply','POST',dict(requestId=proposal['requestId']))[0],409)
        self.assertEqual(self.call(client,'/workspace')[1]['tasks']['value'],[task])

    def test_replanning_rejects_conflicts_with_old_blocks_retained_without_a_replacement(self):
        client=self.client();self.register(client,'replan-conflict@test.local')
        self.assertEqual(self.call(client,'/data/availability','PUT',dict(value=dict(startTime='19:00',endTime='23:00',weekdays=list(range(7))),version=0))[0],200)
        day=(app.datetime.now(app.TZ)+app.timedelta(days=1)).date().isoformat()
        base=dict(title='任务',status='not_started',subtasks=[],due=dict(date=day,precision='date'))
        tasks=[dict(base,id='a',plans=[]),dict(base,id='b',plans=[dict(id='keep',title='必须保留',startAt=day+'T19:00:00+08:00',endAt=day+'T20:00:00+08:00')])]
        self.assertEqual(self.call(client,'/data/tasks','PUT',dict(value=tasks,version=0))[0],200)
        with patch.object(app.planning,'plan',return_value=dict(estimates={'a':60,'b':60},blocks=[dict(taskId='a',title='冲突',startAt=day+'T19:00:00+08:00',endAt=day+'T20:00:00+08:00')],warnings=[])):
            status,result=self.call(client,'/planning/proposals','POST',dict(replan=True))
            self.assertEqual(status,502)
            self.assertIn('时间冲突',result['error'])
        self.assertEqual(self.call(client,'/workspace')[1]['tasks']['value'],tasks)

    def test_recognition_demo_and_atomic_idempotent_confirmation(self):
        a,b=self.client(),self.client();self.register(a,'text-a@test.local');self.register(b,'text-b@test.local')
        status,batches=self.call(a,'/recognition/demo','POST',{})
        self.assertEqual(status,200);self.assertEqual(batches[0]['result']['status'],'partial_success')
        self.assertEqual(self.call(b,'/recognition/batches')[1],[])
        ignored=batches[0]['items'][0]
        status,result=self.call(a,'/recognition/candidates/'+ignored['id']+'/resolve','POST',dict(action='ignore',version=ignored['version']))
        self.assertEqual(status,200)
        ignored=next(candidate for candidate in result['batches'][0]['items'] if candidate['id']==ignored['id'])
        self.assertEqual((ignored['state'],ignored['version']),('ignored',2))
        self.assertEqual(self.call(a,'/recognition/candidates/'+ignored['id']+'/resolve','POST',dict(action='reopen',version=1))[0],409)
        status,result=self.call(a,'/recognition/candidates/'+ignored['id']+'/resolve','POST',dict(action='reopen',version=ignored['version']))
        self.assertEqual(status,200)
        restored=next(candidate for candidate in result['batches'][0]['items'] if candidate['id']==ignored['id'])
        self.assertEqual((restored['state'],restored['version']),('pending',3))
        item=batches[0]['items'][1]
        body=dict(action='create',version=item['version'],tasks_version=0,reviewed=True,target_id=None,draft=dict(title='阅读材料',description='确认无期限任务',due=None))
        self.assertEqual(self.call(b,'/recognition/candidates/'+item['id']+'/resolve','POST',body)[0],404)
        self.assertEqual(self.call(a,'/recognition/candidates/'+item['id']+'/resolve','POST',dict(body,tasks_version=99))[0],409)
        self.assertEqual(self.call(a,'/recognition/candidates/'+item['id']+'/resolve','POST',body)[0],200)
        self.assertEqual(self.call(a,'/recognition/candidates/'+item['id']+'/resolve','POST',body)[0],200)
        self.assertEqual(self.call(a,'/recognition/candidates/'+item['id']+'/resolve','POST',dict(action='reopen',version=item['version']+1))[0],409)
        tasks=self.call(a,'/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks),1);self.assertIsNone(tasks[0]['due']);self.assertEqual(tasks[0]['subtasks'],[])
        self.assertEqual(self.call(a,'/recognition/candidates/'+item['id']+'/resolve','POST',dict(body,action='cancel'))[0],409)
        self.assertEqual(len(self.call(a,'/recognition/demo','POST',{})[1][0]['items']),2)

    def test_recognition_transport_and_request_idempotence(self):
        a=self.client();self.register(a,'text-transport@test.local')
        fixture=json.loads((Path(__file__).parent.parent/'contracts/text-to-app/text-contract-v0.2.json').read_text(encoding='utf-8'))
        captured=[]
        def harness(request,timeout):
            self.assertEqual(timeout,95)
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
        with patch.dict(app.os.environ,{},clear=True), patch.object(app.recognition,'live_options',side_effect=ValueError('missing credential')):
            self.assertEqual(self.call(a,'/recognition/batches','POST',fresh)[0],503)

    def test_explicit_new_task_can_auto_apply_without_claiming_review(self):
        client=self.client();self.register(client,'auto-apply@test.local')
        fixture=json.loads((Path(__file__).parent.parent/'contracts/text-to-app/text-contract-v0.2.json').read_text(encoding='utf-8'))
        manual=copy.deepcopy(fixture)
        manual['request']['request_id']='manual-auto-reject'
        manual['expected']['request_id']='manual-auto-reject'
        for record in manual['request']['records']: record['platform']='manual'
        for candidate in manual['expected']['tasks']:
            for source in candidate['sources']: source['platform']='manual'
        for record in fixture['request']['records']: record['platform']='teaching_web'
        for candidate in fixture['expected']['tasks']:
            for source in candidate['sources']: source['platform']='teaching_web'
        with patch.dict(app.os.environ,{'TEXT_HARNESS_URL':'http://local-test/recognize'}),patch.object(app.recognition,'urlopen',return_value=io.BytesIO(json.dumps(fixture['expected']).encode())):
            status,batches=self.call(client,'/recognition/batches','POST',fixture['request'])
        self.assertEqual(status,200)
        clear=next(item for item in batches[0]['items'] if item['candidate']['candidate_id']=='c1')
        unclear=next(item for item in batches[0]['items'] if item['candidate']['candidate_id']=='c2')
        body=dict(action='create',auto_apply=True,version=clear['version'])
        path='/recognition/candidates/'+clear['id']+'/resolve'
        with patch.dict(app.os.environ,{'TEXT_HARNESS_URL':'http://local-test/recognize'}),patch.object(app.recognition,'urlopen',return_value=io.BytesIO(json.dumps(manual['expected']).encode())):
            manual_status,manual_batches=self.call(client,'/recognition/batches','POST',manual['request'])
        self.assertEqual(manual_status,200)
        manual_candidate=next(item for item in manual_batches[0]['items'] if item['candidate']['candidate_id']=='c1')
        self.assertEqual(self.call(client,'/recognition/candidates/'+manual_candidate['id']+'/resolve','POST',dict(action='create',auto_apply=True,version=manual_candidate['version']))[0],400)
        for platform in ('email','web'):
            unsupported=copy.deepcopy(manual)
            unsupported['request']['request_id']=unsupported['expected']['request_id']=platform+'-auto-reject'
            for record in unsupported['request']['records']: record['platform']=platform
            for candidate in unsupported['expected']['tasks']:
                for source in candidate['sources']: source['platform']=platform
            with patch.dict(app.os.environ,{'TEXT_HARNESS_URL':'http://local-test/recognize'}),patch.object(app.recognition,'urlopen',return_value=io.BytesIO(json.dumps(unsupported['expected']).encode())):
                status,other_batches=self.call(client,'/recognition/batches','POST',unsupported['request'])
            self.assertEqual(status,200)
            other=next(item for item in other_batches[0]['items'] if item['candidate']['candidate_id']=='c1')
            self.assertEqual(self.call(client,'/recognition/candidates/'+other['id']+'/resolve','POST',dict(action='create',auto_apply=True,version=other['version']))[0],400)
        self.assertEqual(self.call(client,path,'POST',body)[0],200)
        self.assertEqual(self.call(client,path,'POST',body)[0],200)
        self.assertEqual(self.call(client,'/recognition/candidates/'+unclear['id']+'/resolve','POST',dict(action='create',auto_apply=True,version=unclear['version']))[0],200)
        tasks=self.call(client,'/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks),2)
        self.assertEqual(tasks[0]['due'],dict(date='2026-09-30',precision='date'))
        self.assertEqual(tasks[0]['source'],'教学网')
        self.assertIsNone(tasks[1]['due'])
        tasks=self.call(client,'/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks),2)

    def test_unknown_model_deadline_is_not_promoted_from_raw_text(self):
        client=self.client();self.register(client,'raw-date@test.local')
        fixture=json.loads((Path(__file__).parent.parent/'contracts/text-to-app/text-contract-v0.2.json').read_text(encoding='utf-8'))
        fixture['request']['request_id']=fixture['expected']['request_id']='raw-date'
        fixture['request']['records'][0]['platform']='teaching_web'
        fixture['expected']['tasks'][0]['sources'][0]['platform']='teaching_web'
        with patch.dict(app.os.environ,{'TEXT_HARNESS_URL':'http://local-test/recognize'}),patch.object(app.recognition,'urlopen',return_value=io.BytesIO(json.dumps(fixture['expected']).encode())):
            status,batches=self.call(client,'/recognition/batches','POST',fixture['request'])
        self.assertEqual(status,200)
        candidate=next(item for item in batches[0]['items'] if item['candidate']['candidate_id']=='c1')
        stored=copy.deepcopy(candidate['candidate'])
        stored['deadline'].update(value=None,precision='unknown',status='unknown',raw='2026.10.11（23:59:00）',basis='时刻格式不明确或时区不可用，保留原话待核实。')
        stored['review_reasons'].append(stored['deadline']['basis'])
        with app.connection() as db: db.execute('UPDATE recognition_candidates SET candidate=? WHERE id=?',(json.dumps(stored),candidate['id']))
        batches=self.call(client,'/recognition/batches')[1]
        candidate=next(item for item in batches[0]['items'] if item['candidate']['candidate_id']=='c1')
        self.assertIsNone(candidate['candidate']['deadline']['value'])
        self.assertEqual(self.call(client,'/recognition/candidates/'+candidate['id']+'/resolve','POST',dict(action='create',auto_apply=True,version=candidate['version']))[0],400)
        self.assertNotIn('tasks',self.call(client,'/workspace')[1])

    def test_f3_classification_auto_apply_respects_review_and_no_date(self):
        client=self.client();self.register(client,'f3-auto@test.local')
        fixture=json.loads((Path(__file__).parent.parent/'contracts/text-to-app/text-classification-v02.json').read_text(encoding='utf-8'))
        with patch.dict(app.os.environ,{'TEXT_HARNESS_URL':'http://local-test/recognize'}),patch.object(app.recognition,'urlopen',return_value=io.BytesIO(json.dumps(fixture['expected']).encode())):
            status,batches=self.call(client,'/recognition/batches','POST',fixture['request'])
        self.assertEqual(status,200)
        batch=batches[0]
        self.assertTrue(any('history_filtered:' in warning for result in batch['result']['record_results'] for warning in result['warnings']))
        self.assertFalse(any(item['candidate']['input_id']=='synthetic:f3:old-yearless' for item in batch['items']))
        items={item['candidate']['input_id']:item for item in batch['items']}
        for input_id in ('synthetic:f3:dated','synthetic:f3:undated'):
            item=items[input_id]
            self.assertEqual(self.call(client,'/recognition/candidates/'+item['id']+'/resolve','POST',dict(action='create',auto_apply=True,version=item['version']))[0],200)
        for input_id in ('synthetic:f3:unknown-age','synthetic:f3:recent-overdue','synthetic:f3:postpone'):
            item=items[input_id]
            self.assertEqual(self.call(client,'/recognition/candidates/'+item['id']+'/resolve','POST',dict(action='create',auto_apply=True,version=item['version']))[0],400)
        precise=items['synthetic:f3:old-but-future']
        candidate=copy.deepcopy(precise['candidate'])
        candidate['deadline'].update(value='2027-01-01T12:00:30+08:00',precision='datetime')
        with app.connection() as db: db.execute('UPDATE recognition_candidates SET candidate=? WHERE id=?',(json.dumps(candidate),precise['id']))
        self.assertEqual(self.call(client,'/recognition/candidates/'+precise['id']+'/resolve','POST',dict(action='create',auto_apply=True,version=precise['version']))[0],400)
        tasks=self.call(client,'/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks),2)
        self.assertEqual(tasks[0]['due'],dict(date='2026-10-01',precision='datetime',time='18:00'))
        self.assertIsNone(tasks[1]['due'])

    def test_source_selection_is_saved_per_account(self):
        a,b=self.client(),self.client()
        self.register(a,'sources-a@test.local');self.register(b,'sources-b@test.local')
        self.assertEqual(self.call(a,'/data/sources','PUT',dict(value=['teaching_web'],version=0))[0],200)
        self.assertEqual(self.call(a,'/workspace')[1]['sources']['value'],['teaching_web'])
        self.assertNotIn('sources',self.call(b,'/workspace')[1])
        self.assertEqual(self.call(a,'/data/sources','PUT',dict(value=['wechat'],version=1))[0],400)

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
    def test_unscheduled_warning_does_not_repeat_specific_reason(self):
        self.result['estimates']['a']=120
        self.result['warnings']=['报告：P0，DDL 前尚缺 60 分钟']
        app.validate_proposal(self.request,self.result)
        self.assertEqual(self.result['warnings'],['报告：P0，DDL 前尚缺 60 分钟'])
    def test_undated_task_has_no_generic_unscheduled_warning(self):
        self.request['tasks'][0]['due']=None
        self.result['blocks']=[]
        app.validate_proposal(self.request,self.result)
        self.assertEqual(self.result['warnings'],[])

if __name__=='__main__': unittest.main()
