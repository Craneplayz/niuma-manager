"""HTTP sync tests with real persistence/recognition validation, synthetic network/model."""
import json
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import app
import source_jobs as jobs
import test_teaching as fixture
from ddl_text.tests.test_teaching_pipeline import example
from contracts.source_sync import check


class SourceJobsTests(unittest.TestCase):
    setUp = fixture.TeachingIntegrationTests.setUp
    call = fixture.TeachingIntegrationTests.call
    model = fixture.TeachingIntegrationTests.model

    def tearDown(self):
        jobs.wait_for_jobs(app.DB)
        fixture.TeachingIntegrationTests.tearDown(self)

    def start(self, operation='op:one'):
        status, run = self.call('/sources/sync-runs', dict(contract_version='source-sync/0.1',
            sources=['teaching_web'], consent=True, operation_id=operation))
        self.assertEqual(status, 202, run)
        return run

    def test_run_view_exposes_separate_association_progress(self):
        with app.connection() as db:
            account=jobs.task_relations.controls.current_account(db,self.uid)
            settings=jobs.task_relations.settings
            settings.update(db,self.uid,account,dict(settings.view(db,self.uid,account),mode='automatic',consent=True),app.ApiError)
            run=jobs.start(db,self.uid,dict(contract_version='source-sync/0.1',sources=['teaching_web'],consent=True,operation_id='synthetic:association'),app.ApiError)
            jobs.save(db,run,'succeeded')
            job=jobs.task_relations.start(db,self.uid,'sync:'+run['run_id'])
            db.execute('INSERT INTO task_relation_windows VALUES(?,?,?,?)',(job,0,'failed','invalid_agent_output'))
            db.execute("UPDATE task_relation_jobs SET state='failed',error='关联未完成' WHERE id=?",(job,))
            view=jobs.view(run,db);check('run',view)
            self.assertEqual(view['state'],'succeeded')
            self.assertEqual((view['association']['state'],view['association']['failed']),('failed',1))
            self.assertNotIn('credential',view['association'])

    def until(self, run_id, predicate):
        for _ in range(250):
            status, run = self.call('/sources/sync-runs/'+run_id)
            self.assertEqual(status, 200, run)
            check('run',run)
            if predicate(run): return run
            time.sleep(.02)
        self.fail('sync did not reach expected state')

    def test_background_parallel_results_auto_apply_and_idempotency(self):
        request, drafts = example(('explicit-complete',)*6); self.drafts.update(drafts)
        gate=threading.Event(); entered=threading.Event(); lock=threading.Lock(); active=0; peak=0
        def model(request, ApiError):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active)
                if active==4: entered.set()
            try:
                gate.wait(5)
                return self.model(request,ApiError)
            finally:
                with lock: active-=1
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))), patch.object(app.teaching if hasattr(app,'teaching') else jobs.teaching,'recognize',side_effect=model):
            first=self.start()
            self.assertTrue(entered.wait(5)); self.assertEqual(peak,4)
            repeat=self.start();self.assertEqual(first['run_id'],repeat['run_id'])
            self.assertEqual(first['run_id'],self.start('op:duplicate')['run_id'])
            status, listing=self.call('/sources/sync-runs');self.assertEqual(status,200)
            self.assertEqual(len(listing['runs']),1)
            self.assertNotIn('credential',json.dumps(listing));self.assertNotIn('user_id',json.dumps(listing))
            gate.set();jobs.wait_for_jobs(app.DB)
        run=self.until(first['run_id'],lambda r:r['state']=='succeeded')
        self.assertEqual(run['counts']['succeeded'],6);self.assertEqual(run['counts']['added'],6)
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),6)
        self.assertEqual(self.start()['run_id'],first['run_id'])
        jobs.wait_for_jobs(app.DB)
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),6)
        with app.connection() as db:
            events=[dict(r) for r in db.execute('SELECT * FROM usage_events WHERE actor=?',(app.usage.actor(self.uid),))]
        self.assertEqual(sum(r['quantity'] for r in events if r['event']=='task_created'),6)
        self.assertEqual(sum(r['event']=='recognition' for r in events),6)
        self.assertEqual(sum(r['event']=='sync' for r in events),1)
        self.assertTrue(all(r['success'] for r in events if r['event'] in ('sync','recognition')))

    def test_stop_discards_inflight_results_without_removing_saved_results(self):
        request,drafts=example(('explicit-complete',)*5);self.drafts.update(drafts)
        gate=threading.Event();entered=threading.Event()
        def model(request, ApiError):
            entered.set();gate.wait(5);return self.model(request,ApiError)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=model):
            run=self.start();self.assertTrue(entered.wait(5))
            status,stopped=self.call('/sources/sync-runs/'+run['run_id']+'/stop',dict(contract_version='source-sync/0.1'))
            self.assertEqual(status,200);self.assertEqual(stopped['state'],'stopped')
            gate.set();jobs.wait_for_jobs(app.DB)
        self.assertFalse(self.call('/recognition/batches')[1])
        self.assertNotIn('tasks',self.call('/workspace')[1])

    def test_one_failure_does_not_rollback_successful_records(self):
        request,drafts=example(('explicit-complete',)*3);self.drafts.update(drafts)
        failed_id=request['records'][1]['record']['input_id']
        def model(request, ApiError):
            if request['records'][0]['record']['input_id']==failed_id:raise ApiError(502,'模拟模型暂时不可用')
            return self.model(request,ApiError)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=model):
            run=self.start();jobs.wait_for_jobs(app.DB)
        result=self.until(run['run_id'],lambda r:r['state']=='partial_success')
        self.assertEqual(result['counts']['failed'],1);self.assertEqual(result['counts']['added'],2)
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),2)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT success FROM usage_events WHERE event="sync"').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM usage_events WHERE event="recognition" AND success=0').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT sum(quantity) FROM usage_events WHERE event="task_created"').fetchone()[0],2)

    def test_switched_credentials_invalidate_late_collection(self):
        request,_=example();gate=threading.Event();entered=threading.Event()
        def crawler(*args,**kwargs):
            entered.set();gate.wait(5);return SimpleNamespace(returncode=0,stdout=json.dumps(request))
        with patch.object(app.source_sync.subprocess,'run',side_effect=crawler):
            run=self.start();self.assertTrue(entered.wait(5))
            self.assertEqual(self.call('/sources/credentials',dict(username='other-campus',password='synthetic-password'))[0],200)
            gate.set();jobs.wait_for_jobs(app.DB)
        self.assertEqual(self.until(run['run_id'],lambda r:r['state']=='failed')['records'],[])
        with app.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM teaching_sources').fetchone()[0],0)

    def test_credentials_changed_mid_recognition_finish_undispatched_records(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                request,drafts=example(('explicit-complete',)*6);self.drafts.update(drafts)
                gate=threading.Event();entered=threading.Event();lock=threading.Lock();count=0
                def model(request,ApiError):
                    nonlocal count
                    with lock:
                        count+=1
                        if count==jobs.CONCURRENCY:entered.set()
                    gate.wait(10)
                    return self.model(request,ApiError)
                with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=model):
                    run=self.start('credentials:'+str(remove))
                    try:
                        self.assertTrue(entered.wait(5))
                        endpoint='/sources/credentials/remove' if remove else '/sources/credentials'
                        body={} if remove else dict(username='other-campus',password='synthetic-password')
                        self.assertEqual(self.call(endpoint,body)[0],200)
                    finally:gate.set();jobs.wait_for_jobs(app.DB)
                result=self.call('/sources/sync-runs/'+run['run_id'])[1]
                check('run',result)
                self.assertEqual(result['counts']['failed'],6)
                self.assertEqual(result['counts']['queued'],0)
                self.assertEqual(result['counts']['running'],0)
                self.assertEqual(result['error']['code'],'source_changed')
                self.assertTrue(all(r['error']['code']=='source_changed' and not r['error']['retryable'] for r in result['records']))
                self.assertEqual(count,jobs.CONCURRENCY)
                self.assertEqual(self.call('/sources/sync-runs/'+run['run_id']+'/retry',dict(contract_version='source-sync/0.1',operation_id='changed:retry:'+str(remove),consent=True))[0],409)
                self.assertFalse(self.call('/workspace')[1].get('tasks',{}).get('value'))

    def test_reused_operation_survives_failure_restart_and_lost_response(self):
        request,_=example();gate=threading.Event();entered=threading.Event()
        def model(*args,**kwargs):
            entered.set();gate.wait(10);raise app.ApiError(502,'合成暂时故障')
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))) as crawler,patch.object(jobs.teaching,'recognize',side_effect=model) as recognize:
            first=self.start('op:first')
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.start('op:lost-response')['run_id'],first['run_id'])
            finally:gate.set();jobs.wait_for_jobs(app.DB)
            app.initialize()
            repeat=self.start('op:lost-response');jobs.wait_for_jobs(app.DB)
            self.assertEqual(repeat['run_id'],first['run_id']);self.assertEqual(repeat['state'],'failed')
            self.assertEqual(crawler.call_count,1);self.assertEqual(recognize.call_count,1)
            self.assertEqual(self.call('/sources/sync-runs/'+first['run_id']+'/retry',dict(contract_version='source-sync/0.1',operation_id='op:lost-response',consent=True))[0],409)
            # Upgrade an older database without operation aliases.
            with app.connection() as db:db.execute('DELETE FROM source_sync_operations')
            app.initialize()
            self.assertEqual(self.start('op:first')['run_id'],first['run_id'])
            jobs.wait_for_jobs(app.DB);self.assertEqual(crawler.call_count,1)

    def test_other_user_cannot_read_stop_or_start_without_consent(self):
        with patch.object(jobs,'launch'):
            run=self.start()
        self.assertEqual(self.call('/sources/sync-runs',dict(contract_version='source-sync/0.1',operation_id='no-consent',sources=['teaching_web'],consent=False))[0],400)
        self.assertEqual(self.call('/auth/register',dict(name='Other',email='other@example.test',password='Synthetic-password!'))[0],200)
        self.assertEqual(self.call('/sources/sync-runs/'+run['run_id'])[0],404)
        self.assertEqual(self.call('/sources/sync-runs/'+run['run_id']+'/stop',dict(contract_version='source-sync/0.1'))[0],404)
        self.assertEqual(self.call('/sources/sync-runs')[1]['runs'],[])

    def test_stopped_but_unfinished_workers_still_count_toward_capacity(self):
        with patch.object(jobs,'_workers',{('test',str(i)):object() for i in range(jobs.RUN_LIMIT)}):
            status,error=self.call('/sources/sync-runs',dict(contract_version='source-sync/0.1',operation_id='full:one',sources=['teaching_web'],consent=True))
            self.assertEqual(status,503);self.assertIn('释放',error['error'])
            self.assertEqual(self.call('/sources/sync-runs')[1]['runs'],[])

    def test_restart_marks_pending_interrupted_without_automatic_model_replay(self):
        with patch.object(jobs,'launch'):
            run=self.start()
        app.initialize()
        result=self.call('/sources/sync-runs/'+run['run_id'])[1]
        self.assertEqual(result['state'],'interrupted')
        self.assertEqual(result['error']['code'],'interrupted')

    def test_retry_only_failed_records_keeps_success_and_user_decisions(self):
        request,drafts=example(('explicit-complete',)*3);self.drafts.update(drafts)
        bad=request['records'][0]['record']['input_id'];calls=[];fail=True
        def model(request,ApiError):
            identity=request['records'][0]['record']['input_id'];calls.append(identity)
            if fail and identity==bad:raise ApiError(502,'暂时失败')
            return self.model(request,ApiError)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))) as crawler,patch.object(jobs.teaching,'recognize',side_effect=model):
            run=self.start();jobs.wait_for_jobs(app.DB)
            previous=self.call('/workspace')[1]['tasks']['value']
            with app.connection() as db:
                previous[0]['title']='用户手改内容'
                db.execute('UPDATE workspace SET value=?,version=version+1 WHERE user_id=? AND name="tasks"',(json.dumps(previous),self.uid))
            fail=False
            payload=dict(contract_version='source-sync/0.1',operation_id='retry:one',consent=True)
            status,retry=self.call('/sources/sync-runs/'+run['run_id']+'/retry',payload)
            self.assertEqual(status,202,retry);jobs.wait_for_jobs(app.DB)
            self.assertEqual(crawler.call_count,1);self.assertEqual(calls.count(bad),2);self.assertEqual(len(calls),4)
            self.assertEqual(self.call('/sources/sync-runs/'+run['run_id']+'/retry',payload)[1]['run_id'],retry['run_id'])
        tasks=self.call('/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks),3);self.assertIn('用户手改内容',[t['title'] for t in tasks])

    def test_stop_after_one_saved_result_preserves_it_and_retries_only_rest(self):
        request,drafts=example(('explicit-complete',)*3);self.drafts.update(drafts)
        first=request['records'][0]['record']['input_id'];gate=threading.Event()
        def model(request,ApiError):
            if request['records'][0]['record']['input_id']!=first:gate.wait(8)
            return self.model(request,ApiError)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=model):
            run=self.start();self.until(run['run_id'],lambda r:r['counts']['succeeded']==1)
            self.call('/sources/sync-runs/'+run['run_id']+'/stop',dict(contract_version='source-sync/0.1'))
            gate.set();jobs.wait_for_jobs(app.DB)
            self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),1)
            status,retry=self.call('/sources/sync-runs/'+run['run_id']+'/retry',dict(contract_version='source-sync/0.1',operation_id='retry:rest',consent=True))
            self.assertEqual(status,202,retry);jobs.wait_for_jobs(app.DB)
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),3)

    def test_incomplete_source_is_not_model_retryable(self):
        with patch.object(jobs,'launch'):
            run=self.start()
        with app.connection() as db:
            raw=jobs.get(db,self.uid,run['run_id'],app.ApiError)
            raw['records']=[dict(request_id='missing:source',label='未读文件',course=None,state='incomplete',added=0,error=jobs.error('source_incomplete','请补全来源'),seconds=1)]
            jobs.save(db,raw,'failed')
        self.assertEqual(self.call('/sources/sync-runs/'+run['run_id']+'/retry',dict(contract_version='source-sync/0.1',operation_id='retry:missing',consent=True))[0],409)

    def test_single_retry_then_remaining_batch_uses_latest_attempt_and_idempotency(self):
        request,drafts=example(('explicit-complete',)*3);self.drafts.update(drafts)
        failed={e['record']['input_id'] for e in request['records'][:2]};calls=[]
        def model(request,ApiError):
            identity=request['records'][0]['record']['input_id'];calls.append(identity)
            if identity in failed:raise ApiError(502,'合成临时故障')
            return self.model(request,ApiError)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))) as crawler,patch.object(jobs.teaching,'recognize',side_effect=model):
            first=self.start();jobs.wait_for_jobs(app.DB)
            old=self.call('/sources/sync-runs/'+first['run_id'])[1]
            failed_ids=[r['request_id'] for r in old['records'] if r['state']=='failed']
            endpoint='/sources/sync-runs/'+first['run_id']+'/retry'
            payload=dict(contract_version='source-sync/0.1',operation_id='retry:selected',consent=True,request_ids=[failed_ids[0]])
            self.assertEqual(self.call(endpoint,dict(payload,request_ids=['other-account:request']))[0],400)
            failed.clear()
            status,child=self.call(endpoint,payload);self.assertEqual(status,202,child);jobs.wait_for_jobs(app.DB)
            self.assertEqual([r['request_id'] for r in child['records']],[failed_ids[0]])
            self.assertEqual(self.call(endpoint,payload)[1]['run_id'],child['run_id'])
            self.assertEqual(self.call(endpoint,dict(payload,request_ids=[failed_ids[1]]))[0],409)
            self.assertEqual(self.call(endpoint,dict(payload,operation_id='retry:stale'))[0],409)
            status,last=self.call(endpoint,dict(contract_version='source-sync/0.1',operation_id='retry:rest',consent=True))
            self.assertEqual(status,202,last);jobs.wait_for_jobs(app.DB)
            self.assertEqual([r['request_id'] for r in last['records']],[failed_ids[1]])
            self.assertEqual(crawler.call_count,1);self.assertEqual(len(calls),5)
            self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),3)
            self.assertEqual(self.call(endpoint,dict(payload,operation_id='retry:again'))[0],409)
            check('runs',self.call('/sources/sync-runs')[1])

    def test_retry_rejects_removed_snapshot_and_cross_account_access(self):
        request,_=example()
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=app.ApiError(502,'合成故障')):
            run=self.start();jobs.wait_for_jobs(app.DB)
        endpoint='/sources/sync-runs/'+run['run_id']+'/retry'
        payload=dict(contract_version='source-sync/0.1',operation_id='retry:removed',consent=True)
        with app.connection() as db:db.execute('DELETE FROM teaching_sources WHERE user_id=?',(self.uid,))
        with patch.object(jobs,'launch') as launch:
            self.assertEqual(self.call(endpoint,payload)[0],409);launch.assert_not_called()
        self.call('/auth/register',dict(name='Other',email='other@example.test',password='Synthetic-password!'))
        self.assertEqual(self.call(endpoint,payload)[0],404)

    def test_configuration_failure_is_not_offered_as_model_retry(self):
        request,_=example()
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'live_options',side_effect=ValueError('synthetic missing configuration')):
            run=self.start();jobs.wait_for_jobs(app.DB)
        result=self.call('/sources/sync-runs/'+run['run_id'])[1]
        self.assertEqual(result['records'][0]['error']['code'],'model_configuration')
        self.assertFalse(result['records'][0]['error']['retryable'])
        self.assertEqual(self.call('/sources/sync-runs/'+run['run_id']+'/retry',dict(contract_version='source-sync/0.1',operation_id='retry:config',consent=True))[0],409)

    def test_temporary_service_503_remains_retryable(self):
        request,_=example()
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=app.ApiError(503,'合成临时不可用')):
            run=self.start();jobs.wait_for_jobs(app.DB)
        result=self.call('/sources/sync-runs/'+run['run_id'])[1]
        self.assertEqual(result['records'][0]['error']['code'],'recognition_failed')
        self.assertTrue(result['records'][0]['error']['retryable'])


if __name__=='__main__':unittest.main()
