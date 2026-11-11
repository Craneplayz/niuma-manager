"""HTTP integration: captured v0.4 snapshot, recognition, cards, auto-apply and retries."""
from copy import deepcopy
import json
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
from unittest.mock import patch

import app
from auth_test_support import registration_payload
import teaching
from jsonschema import Draft202012Validator
from contracts.teaching_io import snapshot_digest
from ddl_text.config import Settings
from ddl_text.teaching_pipeline import process_teaching_request
from ddl_text.tests.test_teaching_pipeline import example, RecordClient
from ddl_text.tests.teaching_cases import empty_reply


class TeachingIntegrationTests(unittest.TestCase):
    def test_competing_stored_result_does_not_inherit_losing_attempt_diagnostics(self):
        import os
        from ddl_text.teaching_pipeline import _failure
        request,synced=self.capture(('explicit-complete',))
        rid=synced['requests'][0]['request_id'];body=dict(contract_version=teaching.RUN,request_id=rid)
        entered=False;attempt={}
        def model(current,ApiError,**kwargs):
            nonlocal entered
            if not entered:
                entered=True
                with app.connection() as other: teaching.run(other,self.uid,body,True,ApiError)
                return self.model(current,ApiError)
            result,policy=self.model(current,ApiError)
            result.update(status='failed',record_results=[_failure(current['records'][0],'agent_timeout',retryable=True,attempted=True)])
            return result,policy
        with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}),patch.object(teaching,'recognize',side_effect=model),app.connection() as db:
            teaching.run(db,self.uid,body,True,app.ApiError,diagnostic_attempt=attempt)
            self.assertEqual(attempt,{})
            saved=json.loads(db.execute('SELECT result FROM recognition_batches WHERE request_id=?',(rid,)).fetchone()[0])
            self.assertEqual(saved['record_results'][0]['error']['code'],'agent_timeout')

    def test_batch_errors_bind_the_producing_attempt_not_the_latest(self):
        import os
        from ddl_text.teaching_pipeline import _failure
        entered=False
        def model(current,ApiError,**kwargs):
            nonlocal entered
            if not entered:
                entered=True
                result,policy=self.model(current,ApiError)
                result.update(status='failed',record_results=[_failure(current['records'][0],'invalid_agent_output',retryable=True,attempted=True)])
                return result,policy
            raise ValueError('later attempt outage')
        # The test environment issues its own session cookie; register under it.
        with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
            self.assertEqual(self.call('/auth/register',dict(name='Synthetic',email='binding@example.test',password='Synthetic-password!'))[0],200)
            self.assertEqual(self.call('/sources/credentials',dict(username='synthetic-course-user',password='synthetic-password'))[0],200)
            with app.connection() as db: uid=db.execute("SELECT id FROM users WHERE email='binding@example.test'").fetchone()['id']
            _,synced=self.capture(('explicit-complete',))
            rid=synced['requests'][0]['request_id'];body=dict(contract_version=teaching.RUN,request_id=rid)
            with patch.object(teaching,'recognize',side_effect=model):
                status,out=self.call('/recognition/retry',body)
                self.assertEqual(status,200,out)
                batch=next(b for b in out if b['request']['request_id']==rid)
                self.assertTrue(batch['result']['errors'])
                # The second run fails and persists its own attempt; the stored result keeps the first.
                # A server-side failure is not malformed user input (HTTP 400).
                self.assertEqual(self.call('/recognition/retry',body)[0],500)
            with app.connection() as db:
                history=teaching.diagnostics.attempts(db,uid,rid)
                later=next(a for a in history if any(e.get('exception_type')=='ValueError' for e in a['events']))
                db.execute("UPDATE recognition_attempt_diagnostics SET created_at='9999' WHERE id=?",(later['attempt_id'],))
            status,out=self.call('/recognition/batches')
            self.assertEqual(status,200)
            batch=next(b for b in out if b['request']['request_id']==rid)
            error=batch['result']['errors'][0]
            self.assertIn('diagnostics',error)
            self.assertIn('invalid_agent_output',error['diagnostics'])
            self.assertNotIn('ValueError',error['diagnostics'])

    def test_upgrade_old_changed_candidate_allows_update_even_when_sync_skips(self):
        request, drafts = example(); self.drafts.update(drafts)
        with app.connection() as db:
            first_sync = teaching.register(db, self.uid, 'synthetic-course-user', [request])
        first = self.run_request(first_sync['requests'][0])['items'][0]
        self.assertEqual(self.call('/recognition/candidates/'+first['id']+'/resolve',
                                  dict(action='create', auto_apply=True, version=1))[0], 200)
        request['records'][0]['record']['text'] += ' Additional requirement.'
        with app.connection() as db:
            changed = teaching.register(db, self.uid, 'synthetic-course-user', [request])
        item = self.run_request(changed['requests'][0])['items'][0]
        # The deployed predecessor saved the guard, but retained create.
        legacy = dict(item['candidate'], notice_type='create')
        with app.connection() as db:
            db.execute('UPDATE recognition_candidates SET candidate=? WHERE id=?', (json.dumps(legacy), item['id']))
        app.initialize(recover=False)
        app.initialize(recover=False)  # Idempotent, including candidate version.
        with app.connection() as db:
            repeated = teaching.register(db, self.uid, 'synthetic-course-user', [request])
        self.assertEqual(repeated['requests'], [])
        batches = self.call('/recognition/batches')[1]
        current = next(c for b in batches for c in b['items'] if c['id'] == item['id'])
        self.assertEqual(current['candidate']['notice_type'], 'uncertain')
        self.assertEqual(current['version'], item['version'])
        self.assertEqual(current['state'], 'pending')
        saved = self.call('/workspace')[1]['tasks']
        endpoint = '/recognition/candidates/'+item['id']+'/resolve'
        self.assertEqual(self.call(endpoint, dict(action='create', auto_apply=True, version=1))[0], 400)
        status, out = self.call(endpoint, dict(action='update', reviewed=True, version=1,
            tasks_version=saved['version'], target_id=saved['value'][0]['id'],
            draft=dict(title='Reviewed update', description='Reviewed', due=None)))
        self.assertEqual(status, 200, out)
        tasks = self.call('/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['id'], saved['value'][0]['id'])
        self.assertEqual(tasks[0]['title'], 'Reviewed update')

    def test_changed_source_allows_explicit_update_or_create_without_overwriting_decisions(self):
        request,drafts=example();self.drafts.update(drafts)
        with app.connection() as db:sync=teaching.register(db,self.uid,'synthetic-course-user',[request])
        first=self.run_request(sync['requests'][0])['items'][0]
        status,out=self.call('/recognition/candidates/'+first['id']+'/resolve',dict(action='create',auto_apply=True,version=1))
        self.assertEqual(status,200,out)
        tasks=self.call('/workspace')[1]['tasks']['value']
        tasks[0].update(title='用户已修改的任务',status='completed',completedAt='2026-10-10')
        original=deepcopy(tasks[0])
        with app.connection() as db:
            db.execute('UPDATE workspace SET value=?,version=version+1 WHERE user_id=? AND name="tasks"',(json.dumps(tasks),self.uid))
            decision=dict(db.execute('SELECT state,version,resolution FROM recognition_candidates WHERE id=?',(first['id'],)).fetchone())
        for action in ('update','create'):
            request['records'][0]['record']['text']+=' 补充要求。'
            with app.connection() as db:sync=teaching.register(db,self.uid,'synthetic-course-user',[request])
            item=self.run_request(sync['requests'][0])['items'][0]
            self.assertEqual(item['candidate']['notice_type'],'uncertain')
            self.assertTrue(item['candidate']['needs_review'])
            saved=self.call('/workspace')[1]['tasks']
            self.assertEqual(saved['value'],[original])
            endpoint='/recognition/candidates/'+item['id']+'/resolve'
            self.assertEqual(self.call(endpoint,dict(action='create',auto_apply=True,version=1))[0],400)
            status,out=self.call(endpoint,dict(action=action,reviewed=True,version=1,tasks_version=saved['version'],
                target_id=original['id'] if action=='update' else None,
                draft=dict(title='核对后'+action,description='已核对变更要求',due=None)))
            self.assertEqual(status,200,out)
            tasks=self.call('/workspace')[1]['tasks']['value']
            self.assertEqual(len(tasks),1 if action=='update' else 2)
            self.assertEqual(tasks[0]['id'],original['id'])
            self.assertEqual(tasks[0]['status'],'completed')
            self.assertEqual(tasks[0]['completedAt'],'2026-10-10')
            if action=='update':
                self.assertEqual(tasks[0]['description'],'已核对变更要求')
                self.assertEqual(len(tasks[0]['recognitionHistory']),2)
                original=deepcopy(tasks[0])
            else:
                self.assertEqual(tasks[0],original)
                self.assertNotEqual(tasks[1]['id'],original['id'])
            with app.connection() as db:
                self.assertEqual(dict(db.execute('SELECT state,version,resolution FROM recognition_candidates WHERE id=?',(first['id'],)).fetchone()),decision)

    def test_changed_source_never_auto_duplicates_prior_task_or_deleted_task(self):
        request,drafts=example();self.drafts.update(drafts)
        for version in range(3):
            request['records'][0]['record']['references'] = ([] if version!=1 else [dict(
                ref_id='file:extra',kind='content_image',label='Changed reference',read_state='unsupported',text=None)])
            with app.connection() as db: sync=teaching.register(db,self.uid,'synthetic-course-user',[request])
            item=self.run_request(sync['requests'][0])['items'][0]
            status,_=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))
            self.assertEqual(status,200 if version==0 else 400)
            self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),1 if version<2 else 0)
            if version==1:
                with app.connection() as db:db.execute('UPDATE workspace SET value=? WHERE user_id=? AND name="tasks"',('[]',self.uid))

    def test_test_environment_keeps_failure_diagnostics_after_retry_and_scopes_endpoint(self):
        from server import recognition_diagnostics as diagnostics
        import os
        with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'test'}):
            self.assertEqual(self.call('/auth/login',dict(email='teaching@example.test',password='Synthetic-password!'))[0],200)
            with app.connection() as db:
                with diagnostics.capture(db,self.uid,'synthetic:failed'):
                    diagnostics.event('result',code='invalid_agent_output',schema={'schema_path':'properties/tasks/items/required','rule':'required'})
                with diagnostics.capture(db,self.uid,'synthetic:failed'):diagnostics.event('result',status='success')
            status,out=self.call('/recognition/diagnostics',dict(request_id='synthetic:failed'))
            self.assertEqual(status,200);self.assertEqual(len(out['attempts']),2)
            self.assertEqual(out['attempts'][1]['events'][0]['code'],'invalid_agent_output')
            with app.connection() as db:self.assertEqual(diagnostics.attempts(db,'other-user','synthetic:failed'),[])
        with patch.dict(os.environ,{'NIUMA_ENVIRONMENT':'production'}):
            self.assertEqual(self.call('/auth/login',dict(email='teaching@example.test',password='Synthetic-password!'))[0],200)
            self.assertEqual(self.call('/recognition/diagnostics',dict(request_id='synthetic:failed'))[0],404)

    def test_shared_notices_and_tasks_auto_apply_through_same_workspace(self):
        cases=json.loads((Path(__file__).parents[1]/'contracts/examples/teaching-content-types-v04.json').read_text(encoding='utf-8'))['cases']
        expected=[]
        for case in cases:
            request=case['request'];record=request['records'][0]['record']
            self.drafts[record['input_id']]=json.dumps(case['draft'])
            with app.connection() as db:sync=teaching.register(db,self.uid,'synthetic-course-user',[request])
            batch=self.run_request(sync['requests'][0])
            for item,draft in zip(batch['items'],case['draft']['tasks']):
                self.assertEqual(item['candidate']['content_type'],draft['content_type'])
                self.assertFalse(item['candidate']['needs_review'])
                status,out=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))
                self.assertEqual(status,200,out)
                expected.append((draft['title'],draft['content_type']))
        saved=self.call('/workspace')[1]['tasks']['value']
        self.assertEqual([(t['title'],t['content_type']) for t in saved],expected)
        for task in saved:
            if task['title']=='选课结果公示':
                self.assertEqual(task['due'],dict(date='2026-10-20',time='18:00',precision='datetime'))
            else:self.assertIsNone(task['due'])
            if task['content_type']=='notice':self.assertTrue(task['planningExcluded'])
        evidence=self.call('/recognition/task-evidence',dict(task_id=saved[0]['id']))[1]
        self.assertIn('理教408',json.dumps(evidence,ensure_ascii=False))

    def test_real_synthetic_model_results_replay_through_http_and_workspace(self):
        fixtures=json.loads((Path(__file__).parents[1]/'contracts/examples/teaching-model-routing-v04-cases.json').read_text(encoding='utf-8'))['cases']
        added=0
        for case in fixtures:
            with self.subTest(case=case['name']):
                with app.connection() as db:sync=teaching.register(db,self.uid,'synthetic-course-user',[case['request']])
                view=sync['requests'][0]
                with app.connection() as db:request=teaching.resolve_snapshot(db,self.uid,view['request_id'],app.ApiError)
                result=deepcopy(case['expected']);result['request_id']=request['request_id']
                row=result['record_results'][0];row['snapshot_id']=request['records'][0]['record']['snapshot_id']
                for d in row['decisions']:d['pair']['record']=deepcopy(request['records'][0]['record'])
                with patch.object(teaching,'recognize',return_value=(result,dict(version=result['policy_version']))):
                    status,batches=self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))
                self.assertEqual(status,200,batches)
                batch=next(b for b in batches if b['request']['request_id']==view['request_id'])
                route=case['expected_classification']['recommendation']
                if route in ('filtered','no_tasks'):
                    self.assertFalse(batch['items']);self.assertFalse(batch['result']['errors'])
                else:
                    item=batch['items'][0]
                    self.assertEqual(item['candidate']['needs_review'],route=='candidate')
                    status,_=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))
                    self.assertEqual(status,200 if route=='task' else 400)
                    added+=route=='task'
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),added)

    def test_partial_model_task_auto_applies_while_candidate_and_filtered_remain_distinct(self):
        request,drafts=example(('explicit-complete',)*3)
        for entry,route in zip(request['records'],('task','candidate','filtered')):
            record=entry['record'];record['body_status']='partial'
            task=json.loads(drafts[record['input_id']])['tasks'][0]
            task.update(recommendation=route,reason='' if route=='task' else '缺少最终截止更正。' if route=='candidate' else '历史作业已失效。')
            drafts[record['input_id']]=json.dumps(dict(tasks=[task]))
        self.drafts.update(drafts)
        with app.connection() as db:sync=teaching.register(db,self.uid,'synthetic-course-user',[request])
        for view,route in zip(sync['requests'],('task','candidate','filtered')):
            batch=self.run_request(view)
            if route=='filtered':
                self.assertFalse(batch['items'])
                self.assertTrue(batch['result']['record_results'][0]['warnings'])
                continue
            item=batch['items'][0]
            self.assertEqual(item['candidate']['needs_review'],route=='candidate')
            status,_=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))
            self.assertEqual(status,200 if route=='task' else 400)
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),1)

    def test_partial_non_action_stays_out_of_review_and_failure_retry(self):
        request,drafts=example();record=request['records'][0]['record']
        record.update(text='10月9日上午课程暂停一次。',body_status='partial')
        self.drafts[record['input_id']]=empty_reply(record)
        with app.connection() as db:sync=teaching.register(db,self.uid,'synthetic-course-user',[request])
        batch=self.run_request(sync['requests'][0])
        self.assertFalse(batch['items'])
        self.assertFalse(batch['result']['errors'])
        self.assertEqual(batch['result']['status'],'no_tasks')

    def test_teaching_task_evidence_survives_import_grouping_and_archive(self):
        capture=self.native_capture('active')
        view=self.sync_native(capture)['requests'][0]
        item=self.run_request(view)['items'][0]
        self.assertEqual(self.call('/recognition/imports',dict(import_id='teaching-import',created_at='2026-10-10T02:00:00Z',source='教学网',request_ids=[view['request_id']]))[0],200)
        self.assertEqual(self.call('/recognition/batches')[1][0]['importId'],'teaching-import')
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],200)
        task=self.call('/workspace')[1]['tasks']['value'][0]
        self.assertEqual(self.call('/recognition/imports/archive',dict(request_ids=[view['request_id']]))[0],200)
        status,out=self.call('/recognition/task-evidence',dict(task_id=task['id']))
        self.assertEqual(status,200)
        evidence=out['evidence'][0]
        entry=capture['request']['records'][0]
        self.assertEqual(evidence['original'],entry['record']['text'])
        self.assertEqual(evidence['sourceUrl'],entry['source']['url'])
        self.assertEqual(evidence['platform'],'teaching_web')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old_db=app.DB
        # Claim the fresh directory before creating crawler fixtures, so the
        # same integration fixture also works with managed test environments.
        app.runtime_environment.validate_storage(Path(self.tmp.name)/'test.sqlite3')
        # Crawler execution is mocked below; its availability check must not
        # depend on a developer's node_modules (the server CI does not install it).
        crawler=Path(self.tmp.name)/'crawler'
        (crawler/'node_modules'/'tsx').mkdir(parents=True)
        self.crawler_patch=patch.object(app.source_sync,'CRAWLER',crawler)
        self.crawler_patch.start();self.addCleanup(self.crawler_patch.stop)
        app.DB=Path(self.tmp.name)/'test.sqlite3'; app.initialize()
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}/api'
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        self.assertEqual(self.call('/auth/register',dict(name='Synthetic',email='teaching@example.test',password='Synthetic-password!'))[0],200)
        self.assertEqual(self.call('/sources/credentials',dict(username='synthetic-course-user',password='synthetic-password'))[0],200)
        with app.connection() as db:self.uid=db.execute('SELECT id FROM users').fetchone()['id']
        self.drafts={}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close();self.thread.join();app.DB=self.old_db;self.tmp.cleanup()

    def call(self,path,body=None):
        if path == '/auth/register':
            body = registration_payload(body, lambda request:self.call('/auth/email-code',request))
        req=Request(self.base+path, None if body is None else json.dumps(body).encode(), {'Content-Type':'application/json'})
        try:
            with self.client.open(req,timeout=120) as response:return response.status,json.load(response)
        except HTTPError as response:return response.code,json.load(response)

    def capture(self,names):
        request,drafts=example(names);self.drafts.update(drafts)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))):
            status,result=self.call('/sources/sync',dict(sources=['teaching_web']))
        self.assertEqual(status,200,result)
        return request,result

    def model(self,request,ApiError):
        # Includes spawn/import startup under parallel load on a 2-vCPU test host.
        # Production deadlines and dedicated timeout regressions stay unchanged.
        output=process_teaching_request(request,client=RecordClient(self.drafts),settings=Settings(timeout_seconds=30),
            verified_snapshot_digests={snapshot_digest(e) for e in request['records']})
        output['policy_version']='teaching-policy-v1:'+'0'*64
        return output,dict(version=output['policy_version'])

    def native_capture(self,state,when='2026-10-10T10:00:00+08:00'):
        request,drafts=example(); self.drafts.update(drafts)
        record=request['records'][0]['record'];record['collected_at']=when
        basis={'submitted':'review_submission_history_title','active':'active_assignment_page','unknown':'unknown'}[state]
        return dict(contract_version='teaching-capture/0.1',request=request,assignment=dict(
            state=state,basis=basis,course_id=record['course_id'],content_id=record['source_item_id']))

    def sync_native(self,capture):
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(capture))):
            status,result=self.call('/sources/sync',dict(sources=['teaching_web']))
        self.assertEqual(status,200,result)
        return result

    def mixed_capture(self,state,when='2026-10-10T10:00:00+08:00'):
        capture=self.native_capture(state,when)
        record=capture['request']['records'][0]['record']
        record['text']='第二次作业：提交练习1至3，2026年10月23日截止。提交后另需阅读补充材料，2026年10月23日截止。'
        first=json.loads(self.drafts[record['input_id']])['tasks'][0]
        first['title']='提交第二次作业';first['description']='提交练习1至3。'
        first['assessment']['action']['evidence'][0]['quote']='第二次作业：提交练习1至3'
        second=deepcopy(first);second.update(title='阅读补充材料',description='提交后阅读补充材料。')
        second['assessment']['action']['evidence'][0]['quote']='提交后另需阅读补充材料'
        self.drafts[record['input_id']]=json.dumps(dict(tasks=[first,second]))
        return capture

    def test_submitted_unknown_scope_is_recognized_but_needs_manual_review(self):
        capture=self.native_capture('submitted')
        result=self.sync_native(capture)
        self.assertEqual(result['summary'],dict(total=1,skipped=0,pending=1))
        item=self.run_request(result['requests'][0])['items'][0]
        self.assertTrue(item['candidate']['needs_review'])
        self.assertIn('尚未明确对应哪些行动',' '.join(item['candidate']['review_reasons']))
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],400)
        self.assertEqual(self.sync_native(capture)['requests'],[])
        # Explicit candidate review remains usable; it does not resolve a source latch.
        version=self.call('/workspace')[1].get('tasks',dict(version=0))['version']
        status,_=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(
            action='create',reviewed=True,version=1,tasks_version=version,
            draft=dict(title='用户确认的行动',description='人工核对后保留',due=None)))
        self.assertEqual(status,200)

    def test_native_submitted_assignment_filtered_without_cross_source_matching(self):
        capture=self.native_capture('submitted')
        iid=capture['request']['records'][0]['record']['input_id']
        draft=json.loads(self.drafts[iid])
        draft['tasks'][0]['submission_relation']='assignment_submission'
        self.drafts[iid]=json.dumps(draft)
        sync=self.sync_native(capture)
        batch=self.run_request(sync['requests'][0])
        self.assertTrue(batch['items'])
        self.assertTrue(all(i['state']=='ignored' for i in batch['items']))
        with app.connection() as db:
            detail=json.loads(db.execute('SELECT detail FROM submission_controls').fetchone()[0])
            self.assertEqual(detail['observation']['snapshot']['state'],'submitted')
            self.assertTrue(all(a['relation']=='assignment_submission' for a in detail['observation']['actions']))

    def prepare_action_limit_history(self):
        capture=self.native_capture('active')
        record=capture['request']['records'][0]['record'];iid=record['input_id']
        template=json.loads(self.drafts[iid])['tasks'][0]
        template.update(submission_relation='assignment_submission',recommendation='candidate',reason='需核对作业细节。')
        for version in range(2):
            record['text']+=f'\n补充说明{version}。'
            record['collected_at']=f'2026-10-10T10:0{version}:00+08:00'
            self.drafts[iid]=json.dumps(dict(tasks=[dict(deepcopy(template),title=f'练习 {version}-{index}') for index in range(50)]))
            self.assertEqual(len(self.run_request(self.sync_native(capture)['requests'][0])['items']),50)
        with app.connection() as db:
            detail=json.loads(db.execute('SELECT detail FROM submission_controls').fetchone()[0])
            self.assertEqual(len(detail['observation']['actions']),100)
            self.assertEqual(detail['observation']['scope'],'mixed')
        self.drafts[iid]=json.dumps(dict(tasks=[template]))
        return capture

    def test_action_limit_does_not_roll_back_recognition(self):
        capture=self.prepare_action_limit_history()
        capture['request']['records'][0]['record']['text']+='\n第三次补充。'
        capture['request']['records'][0]['record']['collected_at']='2026-10-10T10:02:00+08:00'
        batch=self.run_request(self.sync_native(capture)['requests'][0])
        self.assertEqual(len(batch['items']),1);self.assertEqual(batch['items'][0]['state'],'pending')
        self.assertFalse(batch['result']['errors'])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recognition_candidates').fetchone()[0],101)
            self.assertEqual(db.execute('SELECT count(*) FROM recognition_batches').fetchone()[0],3)
            detail=json.loads(db.execute('SELECT detail FROM submission_controls').fetchone()[0])
            self.assertEqual(detail['observation']['scope'],'unknown')

    def test_action_limit_tombstones_do_not_interrupt_reprocessing(self):
        import reprocessing
        capture=self.prepare_action_limit_history()
        iid=capture['request']['records'][0]['record']['input_id']
        source=self.call('/recognition/reprocessing/sources')[1]['accounts'][0]
        with patch.object(reprocessing,'recognize_saved',side_effect=lambda req,*a,**k:self.model(req,app.ApiError)):
            status,run=self.call('/recognition/reprocessing/runs',dict(contract_version='reprocessing/0.1',
                operation_id='action-limit-upgrade',source_account_id=source['source_account_id'],input_ids=[iid]))
            self.assertEqual(status,202,run);self.assertTrue(reprocessing.wait_for_jobs(app.DB))
        saved=self.call('/recognition/reprocessing/runs/'+run['run_id'])[1]
        self.assertEqual(saved['state'],'succeeded',saved)
        with app.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM recognition_candidates WHERE state='pending'").fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM recognition_batches').fetchone()[0],3)
            detail=json.loads(db.execute('SELECT detail FROM submission_controls').fetchone()[0])
            self.assertEqual(detail['observation']['scope'],'unknown')

    def test_submitted_assignment_keeps_independent_reading(self):
        capture=self.mixed_capture('submitted')
        iid=capture['request']['records'][0]['record']['input_id']
        draft=json.loads(self.drafts[iid])
        draft['tasks'][0]['submission_relation']='assignment_submission'
        draft['tasks'][1]['submission_relation']='other'
        self.drafts[iid]=json.dumps(draft)
        batch=self.run_request(self.sync_native(capture)['requests'][0])
        by_title={i['candidate']['title']:i for i in batch['items']}
        self.assertEqual(by_title['提交第二次作业']['state'],'ignored')
        self.assertEqual(by_title['阅读补充材料']['state'],'pending')
        self.assertFalse(by_title['阅读补充材料']['candidate']['needs_review'])

    def test_recognized_native_assignment_completes_when_later_submitted(self):
        capture=self.native_capture('active')
        iid=capture['request']['records'][0]['record']['input_id']
        draft=json.loads(self.drafts[iid]);draft['tasks'][0]['submission_relation']='assignment_submission'
        self.drafts[iid]=json.dumps(draft)
        item=self.run_request(self.sync_native(capture)['requests'][0])['items'][0]
        status,result=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))
        self.assertEqual(status,200,result)
        self.sync_native(self.native_capture('submitted','2026-10-10T11:00:00+08:00'))
        tasks=self.call('/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks),1);self.assertEqual(tasks[0]['status'],'completed')

    def test_reprocessing_legacy_submitted_candidate_binds_new_action_and_withdraws_it(self):
        import reprocessing
        capture=self.native_capture('submitted')
        iid=capture['request']['records'][0]['record']['input_id']
        old=self.run_request(self.sync_native(capture)['requests'][0])['items'][0]
        self.assertEqual(old['state'],'pending')
        draft=json.loads(self.drafts[iid]);draft['tasks'][0]['submission_relation']='assignment_submission'
        self.drafts[iid]=json.dumps(draft)
        source=self.call('/recognition/reprocessing/sources')[1]['accounts'][0]
        with patch.object(reprocessing,'recognize_saved',side_effect=lambda req,*a,**k:self.model(req,app.ApiError)):
            status,run=self.call('/recognition/reprocessing/runs',dict(contract_version='reprocessing/0.1',
                operation_id='native-submitted-upgrade',source_account_id=source['source_account_id'],input_ids=[iid]))
            self.assertEqual(status,202,run);self.assertTrue(reprocessing.wait_for_jobs(app.DB))
        saved=self.call('/recognition/reprocessing/runs/'+run['run_id'])[1]
        self.assertEqual(saved['state'],'succeeded',saved)
        with app.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM recognition_candidates WHERE state='pending'").fetchone()[0],0)

    def recognized_submitted_assignment(self):
        capture=self.native_capture('submitted')
        iid=capture['request']['records'][0]['record']['input_id']
        draft=json.loads(self.drafts[iid]);draft['tasks'][0]['submission_relation']='assignment_submission'
        self.drafts[iid]=json.dumps(draft)
        item=self.run_request(self.sync_native(capture)['requests'][0])['items'][0]
        self.assertEqual((item['state'],item['version']),('ignored',2))
        return capture,item

    def reprocess_native(self, capture, operation):
        import reprocessing
        source=self.call('/recognition/reprocessing/sources')[1]['accounts'][0]
        with patch.object(reprocessing,'recognize_saved',side_effect=lambda req,*a,**k:self.model(req,app.ApiError)):
            status,run=self.call('/recognition/reprocessing/runs',dict(contract_version='reprocessing/0.1',
                operation_id=operation,source_account_id=source['source_account_id'],
                input_ids=[capture['request']['records'][0]['record']['input_id']]))
            self.assertEqual(status,202,run);self.assertTrue(reprocessing.wait_for_jobs(app.DB))
        saved=self.call('/recognition/reprocessing/runs/'+run['run_id'])[1]
        self.assertEqual(saved['state'],'succeeded',saved)
        return saved

    def test_automatic_withdrawal_stays_filtered_after_repeated_reprocessing(self):
        capture,_=self.recognized_submitted_assignment()
        for index in range(2):
            app.initialize()
            saved=self.reprocess_native(capture,f'auto-withdrawal-{index}')
            self.assertEqual(saved['records'][0]['decision_summary']['preserved'],0)
            self.assertEqual(saved['records'][0]['decision_summary']['new_pending'],0)
            with app.connection() as db:
                rows=db.execute('SELECT state,version,resolution FROM recognition_candidates').fetchall()
                self.assertEqual(len(rows),1)
                self.assertEqual((rows[0]['state'],rows[0]['version']),('ignored',2))
                self.assertEqual(json.loads(rows[0]['resolution'])['action'],'submission_policy_completed')

    def test_reopened_automatic_withdrawal_survives_restart_sync_and_reprocessing(self):
        capture,item=self.recognized_submitted_assignment()
        status,out=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='reopen',version=2))
        self.assertEqual(status,200,out)
        for index in range(2):
            app.initialize()
            capture['request']['records'][0]['record']['collected_at']=f'2026-10-10T11:0{index}:00+08:00'
            self.sync_native(capture)
            with app.connection() as db:
                row=db.execute('SELECT state,version FROM recognition_candidates WHERE id=?',(item['id'],)).fetchone()
                self.assertEqual(tuple(row),('pending',3))
        saved=self.reprocess_native(capture,'preserve-reopened')
        self.assertEqual(saved['records'][0]['decision_summary']['preserved'],1)
        with app.connection() as db:
            self.assertEqual(tuple(db.execute('SELECT state,version FROM recognition_candidates WHERE id=?',(item['id'],)).fetchone()),('pending',3))

    def test_reopened_candidate_transfers_pause_to_manually_created_task(self):
        capture,item=self.recognized_submitted_assignment()
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='reopen',version=2))[0],200)
        status,out=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',version=3,
            reviewed=True,tasks_version=0,draft=dict(title='仍需处理的作业',description='用户决定保留',due=None)))
        self.assertEqual(status,200,out)
        app.initialize()
        capture['request']['records'][0]['record']['collected_at']='2026-10-10T11:00:00+08:00'
        self.sync_native(capture)
        tasks=self.call('/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks),1);self.assertEqual(tasks[0]['status'],'not_started')

    def test_manual_ignore_is_not_replaced_as_automatic_withdrawal(self):
        capture=self.native_capture('active')
        iid=capture['request']['records'][0]['record']['input_id']
        draft=json.loads(self.drafts[iid]);draft['tasks'][0]['submission_relation']='assignment_submission'
        self.drafts[iid]=json.dumps(draft)
        item=self.run_request(self.sync_native(capture)['requests'][0])['items'][0]
        body=dict(action='ignore',version=1)
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',body)[0],200)
        saved=self.reprocess_native(capture,'manual-ignore')
        self.assertEqual(saved['records'][0]['decision_summary']['preserved'],1)
        with app.connection() as db:
            row=db.execute('SELECT state,version,resolution FROM recognition_candidates WHERE id=?',(item['id'],)).fetchone()
            self.assertEqual((row['state'],row['version']),('ignored',2))
            self.assertEqual(json.loads(row['resolution']),body)

    def test_submitted_preserves_submission_and_independent_reading_candidates(self):
        sync=self.sync_native(self.mixed_capture('active'))
        batch=self.run_request(sync['requests'][0])
        self.assertEqual({i['candidate']['title'] for i in batch['items']},{'提交第二次作业','阅读补充材料'})
        with app.connection() as db:
            before=[tuple(r) for r in db.execute('SELECT id,candidate,state,version,resolution FROM recognition_candidates ORDER BY id')]
            original=db.execute('SELECT result FROM recognition_batches').fetchone()[0]
        self.sync_native(self.mixed_capture('submitted','2026-10-10T11:00:00+08:00'))
        stored=self.call('/recognition/batches')[1][0]
        self.assertEqual([i['state'] for i in stored['items']],['pending','pending'])
        self.assertTrue(all(i['candidate']['needs_review'] for i in stored['items']))
        self.assertEqual(stored['result']['status'],'success')
        self.assertEqual(len(stored['result']['tasks']),2)
        self.assertTrue(all(c['needs_review'] for c in stored['result']['tasks']))
        with app.connection() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT id,candidate,state,version,resolution FROM recognition_candidates ORDER BY id')],before)
            self.assertEqual(db.execute('SELECT result FROM recognition_batches').fetchone()[0],original)
        for item in stored['items']:
            self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],400)

    def test_submitted_preserves_applied_tasks_and_user_ignored_decisions(self):
        sync=self.sync_native(self.native_capture('active'));view=sync['requests'][0]
        iid=view['records'][0]['input_id'];draft=json.loads(self.drafts[iid]);draft['tasks']*=2;self.drafts[iid]=json.dumps(draft)
        first,second=self.run_request(view)['items']
        self.assertEqual(self.call('/recognition/candidates/'+first['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],200)
        self.assertEqual(self.call('/recognition/candidates/'+second['id']+'/resolve',dict(action='ignore',version=1))[0],200)
        before=self.call('/workspace')[1]['tasks']
        self.sync_native(self.native_capture('submitted','2026-10-10T11:00:00+08:00'))
        self.assertEqual(self.call('/workspace')[1]['tasks'],before)
        states={i['id']:(i['state'],i['version']) for i in self.call('/recognition/batches')[1][0]['items']}
        self.assertEqual(states,{first['id']:('applied',2),second['id']:('ignored',2)})
        self.assertEqual(self.call('/recognition/candidates/'+first['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],200)

    def test_late_model_and_late_capture_cannot_restore_submitted_candidate(self):
        sync=self.sync_native(self.native_capture('active'));view=sync['requests'][0]
        submitted=self.native_capture('submitted','2026-10-10T11:00:00+08:00')
        def complete_during_model(request,ApiError):
            output=self.model(request,ApiError)
            with app.connection() as db:teaching.register(db,self.uid,'synthetic-course-user',[submitted])
            return output
        with patch.object(teaching,'recognize',side_effect=complete_during_model):
            self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)
        self.assertEqual(self.call('/recognition/batches')[1],[])
        self.assertEqual(self.sync_native(self.native_capture('active'))['requests'],[])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT state FROM teaching_submission_sources').fetchone()[0],'submitted')

    def test_submitted_active_submitted_keeps_candidates_and_pauses_model_after_restart(self):
        view=self.sync_native(self.mixed_capture('submitted'))['requests'][0]
        items=self.run_request(view)['items']
        for state,when in [('active','2026-10-10T11:00:00+08:00'),('submitted','2026-10-10T12:00:00+08:00')]:
            with patch.object(teaching,'recognize') as model:
                self.assertEqual(self.sync_native(self.mixed_capture(state,when))['requests'],[])
                with app.connection() as db:
                    teaching.initialize(db)
                    source=db.execute('SELECT * FROM teaching_submission_sources').fetchone()
                    self.assertEqual((source['state'],source['review_pending']),(state,1))
                    rid=db.execute('SELECT request_id FROM teaching_sources').fetchone()[0]
                self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=rid))[0],409)
            model.assert_not_called()
            current=self.call('/recognition/batches')[1][0]['items']
            self.assertEqual([(i['id'],i['state'],i['version']) for i in current],[(i['id'],'pending',1) for i in items])
            self.assertTrue(all('来源核对状态尚未明确解除' in ' '.join(i['candidate']['review_reasons']) for i in current))
            for item in current:
                self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],400)
        # A user decision on one candidate must not clear the source review latch.
        item=items[0]
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='ignore',version=1))[0],200)
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='reopen',version=2))[0],200)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT review_pending FROM teaching_submission_sources').fetchone()[0],1)

    def test_missing_native_state_after_submitted_does_not_falsely_keep_completion(self):
        view=self.sync_native(self.native_capture('submitted'))['requests'][0]
        self.run_request(view)
        capture=self.native_capture('unknown','2026-10-10T11:00:00+08:00');capture['assignment']=None
        self.assertEqual(self.sync_native(capture)['requests'],[])
        item=self.call('/recognition/batches')[1][0]['items'][0]
        self.assertEqual(item['state'],'pending')
        self.assertTrue(item['candidate']['needs_review'])
        with app.connection() as db:
            row=db.execute('SELECT state,review_pending FROM teaching_submission_sources').fetchone()
            self.assertEqual(tuple(row),('unknown',1))

    def test_unknown_scope_submitted_does_not_hide_saved_failure_or_error(self):
        view=self.sync_native(self.native_capture('active'))['requests'][0]
        self.drafts[view['records'][0]['input_id']]='not json'
        self.assertEqual(self.run_request(view)['result']['status'],'failed')
        self.sync_native(self.native_capture('submitted','2026-10-10T11:00:00+08:00'))
        result=self.call('/recognition/batches')[1][0]['result']
        self.assertEqual(result['status'],'failed');self.assertTrue(result['errors'])
        self.assertIn('submission_scope_unknown',' '.join(result['record_results'][0]['warnings']))
        with app.connection() as db:
            original=json.loads(db.execute('SELECT result FROM recognition_batches').fetchone()[0])
            self.assertEqual(original['status'],'failed')

    def test_late_model_cannot_bypass_reopened_source_review_latch(self):
        view=self.sync_native(self.native_capture('submitted'))['requests'][0]
        def state_changes_during_model(request,ApiError):
            output=self.model(request,ApiError)
            with app.connection() as db:
                teaching.register(db,self.uid,'synthetic-course-user',[self.native_capture('active','2026-10-10T11:00:00+08:00')])
                teaching.register(db,self.uid,'synthetic-course-user',[self.native_capture('submitted','2026-10-10T12:00:00+08:00')])
            return output
        with patch.object(teaching,'recognize',side_effect=state_changes_during_model):
            self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)
        self.assertEqual(self.call('/recognition/batches')[1],[])

    def test_assignment_identity_and_browser_supplied_state_are_rejected(self):
        capture=self.native_capture('submitted');capture['assignment']['content_id']='other'
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(capture))):
            self.assertEqual(self.call('/sources/sync',dict(sources=['teaching_web']))[0],502)
        self.assertEqual(self.call('/recognition/batches',self.native_capture('submitted'))[0],400)

    def test_submitted_account_cannot_withdraw_other_accounts_candidates(self):
        view=self.sync_native(self.native_capture('active'))['requests'][0]
        first=self.run_request(view)['items'][0]
        self.call('/sources/credentials',dict(username='another-campus-account',password='synthetic-password'))
        self.sync_native(self.native_capture('submitted','2026-10-10T11:00:00+08:00'))
        self.assertEqual(self.call('/recognition/batches')[1][0]['items'][0]['state'],'pending')
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT version FROM recognition_candidates WHERE id=?',(first['id'],)).fetchone()[0],1)

    def run_request(self,view):
        schema=json.loads((teaching.ROOT/'contracts/schemas/teaching-workspace-v0.1.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator(schema).validate(view)
        with patch.object(teaching,'recognize',side_effect=self.model):
            status,result=self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))
        self.assertEqual(status,200,result)
        batch=next(b for b in result if b['request']['request_id']==view['request_id'])
        Draft202012Validator(schema).validate(batch['result'])
        return batch

    def test_sync_recognize_and_apply_preserves_course_date_and_repeat_decisions(self):
        request,sync=self.capture(('unread-supplement','yearless-current-assignment-term','yearless-unknown-age'))
        for index,view in enumerate(sync['requests']):
            batch=self.run_request(view);item=batch['items'][0];card=item['candidate']
            self.assertEqual(card['course_or_project'],'测试课程')
            self.assertFalse(card['needs_review'])
            self.assertEqual(card['deadline']['value'],'2026-10-23')
            status,result=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))
            self.assertEqual(status,200,result)
            with app.connection() as db:
                row=db.execute('SELECT request,result FROM recognition_batches WHERE request_id=?',(view['request_id'],)).fetchone()
                self.assertEqual(json.loads(row['request'])['contract_version'],'0.4')
                self.assertEqual(json.loads(row['result'])['policy_version'],'teaching-policy-v1:'+'0'*64)
        tasks=self.call('/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks),3);self.assertTrue(all(t['course']=='测试课程' for t in tasks))
        self.assertTrue(all(t['due']==dict(date='2026-10-23',precision='date') for t in tasks))
        _,again=self.capture(('unread-supplement','yearless-current-assignment-term','yearless-unknown-age'))
        self.assertEqual(again['summary'],dict(total=3,skipped=3,pending=0))

    def test_stored_unknown_age_card_refreshes_date_without_changing_user_state(self):
        _,sync=self.capture(('yearless-unknown-age',))
        batch=self.run_request(sync['requests'][0]); item=batch['items'][0]
        with app.connection() as db:
            legacy=deepcopy(item['candidate']);legacy['deadline'].update(status='unknown',value=None,precision='unknown')
            db.execute('UPDATE recognition_candidates SET candidate=? WHERE id=?',(json.dumps(legacy),item['id']))
        current=self.call('/recognition/batches')[1][0]['items'][0]
        self.assertEqual(current['id'],item['id'])
        self.assertEqual((current['state'],current['version']),('pending',1))
        self.assertEqual(current['candidate']['deadline']['value'],'2026-10-23')
        self.assertEqual(current['candidate']['deadline']['status'],'explicit')
        self.assertFalse(current['candidate']['needs_review'])

    def test_same_quote_merges_action_and_deadline_support_without_losing_native_proof(self):
        request,sync=self.capture(('explicit-complete',))
        record=request['records'][0]['record'];iid=record['input_id']
        draft=json.loads(self.drafts[iid]);quote=dict(part_id='body',quote=record['text'])
        for field in ('action','deadline'):draft['tasks'][0]['assessment'][field]['evidence']=[quote]
        self.drafts[iid]=json.dumps(draft)
        batch=self.run_request(sync['requests'][0])
        proof=batch['items'][0]['candidate']['evidence']
        self.assertEqual(len(proof),1)
        self.assertEqual(proof[0]['supports'],['title','description','deadline'])
        with app.connection() as db:
            raw=json.loads(db.execute('SELECT result FROM recognition_batches').fetchone()['result'])
        assessment=raw['record_results'][0]['decisions'][0]['pair']['assessment']
        self.assertEqual(assessment['action']['evidence'],[quote])
        self.assertEqual(assessment['deadline']['evidence'],[quote])

    def test_old_overdue_results_and_user_decisions_are_not_reclassified_on_read(self):
        _,sync=self.capture(('explicit-complete','explicit-complete','explicit-complete'))
        expected={}
        for view,state in zip(sync['requests'],('pending','applied','ignored')):
            batch=self.run_request(view);item=batch['items'][0]
            with app.connection() as db:
                row=db.execute('SELECT request,result FROM recognition_batches WHERE request_id=?',(view['request_id'],)).fetchone()
                request,result=json.loads(row['request']),json.loads(row['result'])
                decision=result['record_results'][0]['decisions'][0]
                # Reproduce a result stored by the earlier 30-day policy.
                request['records'][0]['record']['collected_at']='2026-10-24T12:00:00+08:00'
                decision['pair']['record']['collected_at']='2026-10-24T12:00:00+08:00'
                decision.update(recommendation='candidate',review_reasons=['截止已过，是否仍需执行请核对原通知。'])
                result.pop('classification_mode',None)
                raw=json.dumps(result)
                db.execute('UPDATE recognition_batches SET request=?,result=? WHERE request_id=?',
                           (json.dumps(request),raw,view['request_id']))
                db.execute('UPDATE recognition_candidates SET state=? WHERE id=?',(state,item['id']))
                expected[view['request_id']]=(state,raw,item['id'])
        with patch.object(teaching,'recognize') as model:
            batches=self.call('/recognition/batches')[1]
        model.assert_not_called()
        for batch in batches:
            state,raw,item_id=expected[batch['request']['request_id']]
            self.assertEqual(len(batch['result']['tasks']),1)
            self.assertEqual(batch['result']['record_results'][0]['status'],'success')
            self.assertEqual([i['state'] for i in batch['items']],[state])
            with app.connection() as db:
                self.assertEqual(db.execute('SELECT result FROM recognition_batches WHERE request_id=?',
                    (batch['request']['request_id'],)).fetchone()['result'],raw)
                saved=db.execute('SELECT state,version FROM recognition_candidates WHERE id=?',(item_id,)).fetchone()
                self.assertEqual((saved['state'],saved['version']),(state,1))

    def test_failed_record_retry_preserves_other_success_and_no_duplicate_cards(self):
        _,sync=self.capture(('explicit-complete','explicit-complete'))
        bad=sync['requests'][0];iid=bad['records'][0]['input_id'];valid=self.drafts[iid];self.drafts[iid]='invalid JSON'
        self.assertEqual(self.run_request(bad)['result']['status'],'failed')
        good=self.run_request(sync['requests'][1]);original=good['items'][0]['id']
        self.drafts[iid]=valid
        self.assertEqual(self.run_request(bad)['result']['status'],'success')
        self.assertEqual(self.run_request(sync['requests'][1])['items'][0]['id'],original)

    def test_browser_cannot_grant_trust_and_stale_or_removed_sources_cannot_apply(self):
        request,sync=self.capture(('explicit-complete',));view=sync['requests'][0]
        self.assertEqual(self.call('/recognition/batches',request)[0],400)
        self.assertEqual(self.call('/recognition/batches',dict(contract_version=teaching.RUN,request_id=view['request_id'],records=[]))[0],400)
        batch=self.run_request(view);item=batch['items'][0]
        newer=deepcopy(request);newer['records'][0]['record']['text']+=' 新增备注。'
        with app.connection() as db:teaching.register(db,self.uid,'synthetic-course-user',[newer])
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],409)
        self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)
        self.call('/sources/credentials/remove',{})
        self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)

    def test_incomplete_source_is_preserved_but_does_not_repeat_model_until_capture_changes(self):
        request,sync=self.capture(('unread-supplement',))
        view=sync['requests'][0];iid=view['records'][0]['input_id']
        self.drafts[iid]=empty_reply(request['records'][0]['record'],'incomplete')
        batch=self.run_request(view)
        self.assertEqual(batch['result']['errors'][0]['code'],'incomplete_source')
        self.assertFalse(batch['result']['errors'][0]['retryable'])
        with patch.object(teaching,'recognize') as model:
            status,result=self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))
            self.assertEqual(status,409);self.assertIn('不会补抓',result['error'])
            # Collection time alone does not make the same missing data retryable.
            unchanged=deepcopy(request);unchanged['records'][0]['record']['collected_at']='2026-10-11T10:00:00+08:00'
            with app.connection() as db:
                again=teaching.register(db,self.uid,'synthetic-course-user',[unchanged])
            self.assertEqual(again['summary'],dict(total=1,skipped=1,pending=0))
        model.assert_not_called()
        self.assertEqual(self.call('/recognition/batches')[1][0],batch)
        complete=deepcopy(unchanged);record=complete['records'][0]['record']
        record.update(text=record['text']+'\n补读内容：只是课程资料。',body_status='complete',references=[])
        with app.connection() as db:
            refreshed=teaching.register(db,self.uid,'synthetic-course-user',[complete])
        fresh=refreshed['requests'][0]
        self.assertNotEqual(fresh['request_id'],view['request_id'])
        self.drafts[iid]=empty_reply(record)
        self.assertEqual(self.run_request(fresh)['result']['status'],'no_tasks')
        self.assertEqual(len(self.call('/recognition/batches')[1]),2)

    def test_no_tasks_and_multiple_tasks_are_distinct(self):
        request,sync=self.capture(('explicit-complete','explicit-complete'))
        first,second=sync['requests'];self.drafts[first['records'][0]['input_id']]=empty_reply(request['records'][0]['record'])
        iid=second['records'][0]['input_id'];draft=json.loads(self.drafts[iid]);draft['tasks']*=2;self.drafts[iid]=json.dumps(draft)
        self.assertEqual(self.run_request(first)['result']['status'],'no_tasks')
        batch=self.run_request(second);self.assertEqual(len(batch['items']),2)
        self.assertNotEqual(batch['items'][0]['candidate']['candidate_id'],batch['items'][1]['candidate']['candidate_id'])

    def test_another_user_cannot_run_a_captured_snapshot(self):
        _,sync=self.capture(('explicit-complete',));view=sync['requests'][0]
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        self.call('/auth/register',dict(name='Other',email='other@example.test',password='Synthetic-password!'))
        self.call('/sources/credentials',dict(username='synthetic-course-user',password='synthetic-password'))
        with patch.object(teaching,'recognize') as model:
            self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)
        model.assert_not_called()

    def test_account_change_during_model_execution_discards_late_result(self):
        _,sync=self.capture(('explicit-complete',));view=sync['requests'][0]
        def change_account(request,ApiError):
            result=self.model(request,ApiError)
            with app.connection() as db:
                db.execute('UPDATE source_credentials SET username=? WHERE user_id=?',('different-course-account',self.uid))
            return result
        with patch.object(teaching,'recognize',side_effect=change_account):
            self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)
        self.assertEqual(self.call('/recognition/batches')[1],[])
