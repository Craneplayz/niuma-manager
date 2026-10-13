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

    def test_other_user_cannot_read_stop_or_start_without_consent(self):
        with patch.object(jobs,'launch'):
            run=self.start()
        self.assertEqual(self.call('/sources/sync-runs',dict(contract_version='source-sync/0.1',operation_id='no-consent',sources=['teaching_web'],consent=False))[0],400)
        self.assertEqual(self.call('/auth/register',dict(name='Other',email='other@example.test',password='Synthetic-password!'))[0],200)
        self.assertEqual(self.call('/sources/sync-runs/'+run['run_id'])[0],404)
        self.assertEqual(self.call('/sources/sync-runs/'+run['run_id']+'/stop',dict(contract_version='source-sync/0.1'))[0],404)
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


if __name__=='__main__':unittest.main()
