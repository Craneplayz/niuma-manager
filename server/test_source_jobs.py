"""HTTP sync tests with real persistence/recognition validation, synthetic network/model."""
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor

import app
import source_jobs as jobs
import test_teaching as fixture
from ddl_text.tests.test_teaching_pipeline import example
from ddl_text.tests.teaching_cases import empty_reply
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
        with patch.object(jobs,'_workers',{('test',str(i)):object() for i in range(jobs.RUN_LIMIT+jobs.QUEUE_LIMIT)}):
            status,error=self.call('/sources/sync-runs',dict(contract_version='source-sync/0.1',operation_id='full:one',sources=['teaching_web'],consent=True))
            self.assertEqual(status,503);self.assertIn('释放',error['error'])
            self.assertEqual(self.call('/sources/sync-runs')[1]['runs'],[])

    def test_fifth_account_waits_without_blocking_workspace_or_exceeding_execution_limit(self):
        clients=[self.client]
        for index in range(1,5):
            self.client=build_opener(HTTPCookieProcessor(CookieJar()))
            self.assertEqual(self.call('/auth/register',dict(name='Synthetic',email=f'capacity{index}@example.test',password='Synthetic-password!'))[0],200)
            self.assertEqual(self.call('/sources/credentials',dict(username='synthetic-course-user',password='synthetic-password'))[0],200)
            clients.append(self.client)
        request,drafts=example(('explicit-complete',));self.drafts.update(drafts)
        gate=threading.Event();entered=threading.Event();lock=threading.Lock()
        active=peak=0
        def crawl(*args,**kwargs):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active)
                if active==jobs.RUN_LIMIT:entered.set()
            try:
                if not gate.wait(8):raise RuntimeError('synthetic crawl release timed out')
                return SimpleNamespace(returncode=0,stdout=json.dumps(request))
            finally:
                with lock:active-=1
        runs=[]
        with patch.object(app.source_sync.subprocess,'run',side_effect=crawl),patch.object(jobs.teaching,'recognize',side_effect=self.model):
            try:
                for index,client in enumerate(clients):
                    self.client=client;runs.append(self.start(f'capacity:{index}'))
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.call('/sources/sync-runs/'+runs[-1]['run_id'])[1]['state'],'queued')
                self.assertEqual(self.call('/workspace')[0],200)
                self.assertEqual(self.call('/sources/sync-runs/'+runs[0]['run_id'])[0],404)
            finally:gate.set()
            jobs.wait_for_jobs(app.DB)
        self.assertEqual(peak,jobs.RUN_LIMIT)
        for client,run in zip(clients,runs):
            self.client=client
            self.assertEqual(self.until(run['run_id'],lambda r:r['state']=='succeeded')['counts']['added'],1)
            tasks=self.call('/workspace')[1]['tasks']['value']
            self.assertEqual(len(tasks),1)
            self.assertEqual(self.start(run['operation_id'])['run_id'],run['run_id'])
            self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),1)

    def test_stopped_waiting_run_releases_ticket_before_execution_slot_is_free(self):
        queue=jobs.ModelQueue(limit=1,pending=1,wait=5)
        self.assertTrue(queue.acquire())
        try:
            with patch.object(jobs,'_slots',queue),patch.object(app.source_sync.subprocess,'run') as crawler:
                run=self.start('stop:waiting')
                with queue.condition:
                    self.assertTrue(queue.condition.wait_for(lambda:len(queue.waiters)==1,timeout=3))
                status,stopped=self.call('/sources/sync-runs/'+run['run_id']+'/stop',dict(contract_version='source-sync/0.1'))
                self.assertEqual(status,200);self.assertEqual(stopped['state'],'stopped')
                jobs.wait_for_jobs(app.DB,timeout=3)
                self.assertEqual(len(queue.waiters),0)
                self.assertEqual(queue.active,1)
                crawler.assert_not_called()
        finally:queue.release()

    def test_waiting_run_timeout_is_retryable_and_does_not_call_crawler(self):
        queue=jobs.ModelQueue(limit=1,pending=1,wait=.05)
        self.assertTrue(queue.acquire())
        try:
            with patch.object(jobs,'_slots',queue),patch.object(app.source_sync.subprocess,'run') as crawler:
                run=self.start('timeout:waiting');jobs.wait_for_jobs(app.DB)
                failed=self.until(run['run_id'],lambda r:r['state']=='failed')
                self.assertTrue(failed['error']['retryable'])
                self.assertIn('排队',failed['error']['message'])
                self.assertEqual(len(queue.waiters),0)
                crawler.assert_not_called()
        finally:queue.release()

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

    def test_recovery_collects_only_exceptions_and_preserves_success_review_and_unrelated_sources(self):
        request,drafts=example(('explicit-complete',)*4)
        ids=[entry['record']['input_id'] for entry in request['records']]
        self.drafts.update(drafts)
        pending=json.loads(self.drafts[ids[1]]);pending['tasks'][0].update(recommendation='candidate',reason='需要核对截止日期')
        self.drafts[ids[1]]=json.dumps(pending)
        request['records'][3]['record']['body_status']='partial'
        self.drafts[ids[3]]=empty_reply(request['records'][3]['record'],'incomplete')
        calls=[];failing=True
        def model(body,ApiError):
            rid=body['records'][0]['record']['input_id'];calls.append(rid)
            if failing and rid==ids[2]:raise ApiError(502,'合成识别失败')
            return self.model(body,ApiError)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))) as crawler,patch.object(jobs.teaching,'recognize',side_effect=model),patch.object(app.source_sync,'collect',wraps=app.source_sync.collect) as collect:
            first=self.start();jobs.wait_for_jobs(app.DB)
            old=self.call('/sources/sync-runs/'+first['run_id'])[1]
            self.assertEqual((old['counts']['added'],old['counts']['review'],old['counts']['failed'],old['counts']['incomplete']),(1,1,1,1))
            before=self.call('/workspace')[1]['tasks']['value'];before[0]['title']='用户已经改过的成功任务'
            with app.connection() as db:db.execute('UPDATE workspace SET value=?,version=version+1 WHERE user_id=? AND name="tasks"',(json.dumps(before),self.uid))
            review_before=[item for batch in self.call('/recognition/batches')[1] for item in batch['items'] if item['state']=='pending']
            # Simulate an older receipt without input_id: resolve its stable source from storage.
            with app.connection() as db:
                raw=jobs.get(db,self.uid,first['run_id'],app.ApiError)
                for item in raw['records']:item.pop('input_id',None)
                db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?',(json.dumps(raw),first['run_id']))
            refreshed,more=example(('explicit-complete',)*5)
            refreshed['records'][3]['record']['text']+='\n附件已完整取得。'
            self.drafts[ids[3]]=drafts[ids[3]];self.drafts.update({key:value for key,value in more.items() if key not in ids})
            crawler.return_value=SimpleNamespace(returncode=0,stdout=json.dumps(refreshed));failing=False
            selected=[item['request_id'] for item in old['records'] if item['state'] in ('failed','incomplete')]
            endpoint='/sources/sync-runs/'+first['run_id']+'/recover'
            payload=dict(contract_version='source-sync/0.1',operation_id='recover:mixed',consent=True,request_ids=selected)
            status,child=self.call(endpoint,payload);self.assertEqual(status,202,child);jobs.wait_for_jobs(app.DB)
            repaired=self.call('/sources/sync-runs/'+child['run_id'])[1];check('run',repaired)
            self.assertEqual(repaired['state'],'succeeded');self.assertEqual(repaired['counts']['added'],2)
            self.assertEqual(collect.call_args.kwargs['input_ids'],set(ids[2:]))
            self.assertEqual({record['input_id'] for record in repaired['records']},set(ids[2:]))
            self.assertEqual(calls.count(ids[0]),1);self.assertEqual(calls.count(ids[1]),1)
            self.assertEqual(calls.count(ids[2]),2);self.assertEqual(calls.count(ids[3]),2);self.assertEqual(len(calls),6)
            tasks=self.call('/workspace')[1]['tasks']['value'];self.assertEqual(len(tasks),3)
            self.assertIn('用户已经改过的成功任务',[task['title'] for task in tasks])
            review_after=[item for batch in self.call('/recognition/batches')[1] for item in batch['items'] if item['state']=='pending']
            self.assertEqual(review_after,review_before)
            with app.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM teaching_sources WHERE user_id=?',(self.uid,)).fetchone()[0],4)
            recovered=self.call('/sources/sync-runs/'+first['run_id'])[1]
            self.assertTrue(all(item.get('recovered') for item in recovered['records'] if item['state'] in ('failed','incomplete')))
            self.assertNotIn('recovery_targets',repaired);self.assertNotIn('recover_sources',repaired)
            self.assertEqual(self.call(endpoint,payload)[1]['run_id'],child['run_id']);self.assertEqual(crawler.call_count,2)
            app.initialize()
            self.assertEqual(self.call(endpoint,payload)[1]['run_id'],child['run_id'])
            self.assertEqual(self.call(endpoint.replace('/recover','/retry'),payload)[0],409)

    def test_recovery_keeps_unchanged_incomplete_sources_unresolved_without_repeating_model_calls(self):
        request,_=example();record=request['records'][0]['record'];record['body_status']='partial'
        self.drafts[record['input_id']]=empty_reply(record,'incomplete')
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=self.model) as model:
            first=self.start();jobs.wait_for_jobs(app.DB)
            status,child=self.call('/sources/sync-runs/'+first['run_id']+'/recover',dict(contract_version='source-sync/0.1',operation_id='recover:still-missing',consent=True))
            self.assertEqual(status,202,child);jobs.wait_for_jobs(app.DB)
            result=self.call('/sources/sync-runs/'+child['run_id'])[1]
            self.assertEqual(result['counts']['incomplete'],1);self.assertEqual(result['counts']['added'],0)
            self.assertEqual(model.call_count,1)
            self.assertFalse(self.call('/sources/sync-runs/'+first['run_id'])[1]['records'][0].get('recovered'))

    def test_recovery_collection_outage_still_retries_recognition_and_keeps_missing_source_unresolved(self):
        request,drafts=example(('explicit-complete',)*2);self.drafts.update(drafts)
        ids=[entry['record']['input_id'] for entry in request['records']]
        request['records'][1]['record']['body_status']='partial'
        self.drafts[ids[1]]=empty_reply(request['records'][1]['record'],'incomplete')
        failing=True
        def model(body,ApiError):
            if failing and body['records'][0]['record']['input_id']==ids[0]:raise ApiError(502,'暂时识别失败')
            return self.model(body,ApiError)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))) as crawler,patch.object(jobs.teaching,'recognize',side_effect=model) as recognizer:
            first=self.start();jobs.wait_for_jobs(app.DB)
            crawler.side_effect=OSError('synthetic collection unavailable');failing=False
            status,child=self.call('/sources/sync-runs/'+first['run_id']+'/recover',dict(contract_version='source-sync/0.1',operation_id='recover:collection-outage',consent=True))
            self.assertEqual(status,202,child);jobs.wait_for_jobs(app.DB)
            result=self.call('/sources/sync-runs/'+child['run_id'])[1]
            self.assertEqual(result['state'],'partial_success');self.assertEqual(result['counts']['added'],1)
            self.assertEqual(result['counts']['incomplete'],1);self.assertEqual(result['error']['code'],'collection_failed')
            self.assertEqual(recognizer.call_count,3)

    def test_recovery_resolves_all_predecessors_after_several_incomplete_snapshots(self):
        request,drafts=example();record=request['records'][0]['record'];identity=record['input_id'];record['body_status']='partial'
        self.drafts[identity]=empty_reply(record,'incomplete')
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))) as crawler,patch.object(jobs.teaching,'recognize',side_effect=self.model):
            first=self.start();jobs.wait_for_jobs(app.DB)
            record['text']+='\n只补齐了一部分内容。';crawler.return_value.stdout=json.dumps(request)
            status,middle=self.call('/sources/sync-runs/'+first['run_id']+'/recover',dict(contract_version='source-sync/0.1',operation_id='recover:partial',consent=True))
            self.assertEqual(status,202,middle);jobs.wait_for_jobs(app.DB)
            self.assertEqual(self.call('/sources/sync-runs/'+middle['run_id'])[1]['counts']['incomplete'],1)
            self.assertEqual([run['run_id'] for run in self.call('/sources/sync-runs')[1]['runs']],[middle['run_id']])
            record['text']+='\n最终附件已完整取得。';record['body_status']='complete';crawler.return_value.stdout=json.dumps(request);self.drafts[identity]=drafts[identity]
            status,last=self.call('/sources/sync-runs/'+middle['run_id']+'/recover',dict(contract_version='source-sync/0.1',operation_id='recover:complete',consent=True))
            self.assertEqual(status,202,last);jobs.wait_for_jobs(app.DB)
            self.assertEqual(self.call('/sources/sync-runs/'+last['run_id'])[1]['counts']['added'],1)
            last_record=self.call('/sources/sync-runs/'+last['run_id'])[1]['records'][0]
            for previous in (first,middle):
                restored=self.call('/sources/sync-runs/'+previous['run_id'])[1]['records'][0]
                self.assertTrue(restored['recovered'])
                self.assertEqual(restored['recovered_by'],dict(run_id=last['run_id'],request_id=last_record['request_id']))
            listing=self.call('/sources/sync-runs')[1];check('runs',listing)
            self.assertEqual([run['run_id'] for run in listing['runs']],[last['run_id'],middle['run_id']])
            self.assertTrue(listing['runs'][1]['records'][0]['recovered'])
            batches=self.call('/recognition/batches')[1]
            self.assertEqual(len(batches),3)
            self.assertTrue(all(batch['sourceSyncManaged'] for batch in batches))

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


    def test_no_change_receipt_is_hidden_bounded_idempotent_and_keeps_dedup(self):
        request,drafts=example()
        rid=request['records'][0]['record']['input_id']
        self.drafts[rid]=empty_reply(request['records'][0]['record'])
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))) as crawler, patch.object(jobs.teaching,'recognize',side_effect=self.model) as model:
            first=self.start('quiet:first');jobs.wait_for_jobs(app.DB)
            quiet=self.until(first['run_id'],lambda r:r['state']=='succeeded')
            self.assertEqual(quiet['records'],[]);self.assertEqual(quiet['counts']['review'],0)
            listing=self.call('/sources/sync-runs')[1];check('runs',listing)
            self.assertEqual(listing['runs'],[]);self.assertFalse(listing['last_check']['changed'])
            self.assertEqual(listing['last_check']['run_id'],first['run_id'])
            self.assertEqual(self.start('quiet:first')['run_id'],first['run_id']);jobs.wait_for_jobs(app.DB)
            self.assertEqual(crawler.call_count,1)
            again=self.start('quiet:second');jobs.wait_for_jobs(app.DB)
            self.assertEqual(model.call_count,1)
            self.assertEqual(self.call('/sources/sync-runs')[1]['runs'],[])
            with app.connection() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM source_sync_quiet_runs').fetchone()[0],2)
                stored=json.loads(db.execute('SELECT snapshot FROM source_sync_runs WHERE run_id=?',(again['run_id'],)).fetchone()[0])
                self.assertEqual(stored['records'],[])
                source_count=db.execute('SELECT count(*) FROM teaching_sources').fetchone()[0]
                batch_count=db.execute('SELECT count(*) FROM recognition_batches').fetchone()[0]
                jobs.prune_quiet(db,now=time.time()+jobs.QUIET_RECEIPT_SECONDS+1)
                self.assertEqual(db.execute('SELECT count(*) FROM source_sync_runs').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM source_sync_operations').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM teaching_sources').fetchone()[0],source_count)
                self.assertEqual(db.execute('SELECT count(*) FROM recognition_batches').fetchone()[0],batch_count)
            self.assertEqual(self.call('/sources/sync-runs')[1]['last_check']['run_id'],again['run_id'])
            self.start('quiet:third');jobs.wait_for_jobs(app.DB);self.assertEqual(model.call_count,1)

    def test_mixed_task_candidate_notice_and_filtered_results_keep_only_useful_records(self):
        request,drafts=example(('explicit-complete',)*4)
        for entry,route in zip(request['records'],('task','candidate','task','filtered')):
            rid=entry['record']['input_id'];draft=json.loads(drafts[rid]);card=draft['tasks'][0]
            card.update(recommendation=route,reason='' if route=='task' else '合成待核对理由' if route=='candidate' else '合成无需保留内容')
            if entry is request['records'][2]:card['content_type']='notice'
            self.drafts[rid]=json.dumps(draft)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=self.model):
            run=self.start();jobs.wait_for_jobs(app.DB)
        result=self.until(run['run_id'],lambda r:r['state']=='succeeded')
        self.assertEqual(result['counts']['added'],2);self.assertEqual(result['counts']['review'],1)
        self.assertEqual(len(result['records']),3)
        self.assertEqual(len(self.call('/sources/sync-runs')[1]['runs']),1)
        tasks=self.call('/workspace')[1]['tasks']['value']
        self.assertTrue(any(task['content_type']=='notice' for task in tasks))
        receipts=[item for record in result['records'] for item in record['added_items']]
        self.assertEqual({item['task_id'] for item in receipts},{task['id'] for task in tasks})
        self.assertEqual({item['label'] for item in receipts},{task['title'] for task in tasks})
        reviews=[item for record in result['records'] for item in record['review_items']]
        candidates=[item for batch in self.call('/recognition/batches')[1] for item in batch['items'] if item['state']=='pending']
        self.assertEqual({item['candidate_id'] for item in reviews},{item['id'] for item in candidates})
        self.assertEqual({item['label'] for item in reviews},{item['candidate']['title'] for item in candidates})

    def test_split_results_have_individual_receipts_and_upgrade_preserves_workspace(self):
        request,drafts=example();rid=request['records'][0]['record']['input_id']
        draft=json.loads(drafts[rid]);second=json.loads(json.dumps(draft['tasks'][0]))
        draft['tasks'][0]['title']='第一项任务';second['title']='第二项任务'
        draft['tasks'].append(second);self.drafts[rid]=json.dumps(draft)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=self.model):
            run=self.start();jobs.wait_for_jobs(app.DB)
        result=self.until(run['run_id'],lambda r:r['state']=='succeeded')
        self.assertEqual(len(result['records']),1)
        self.assertEqual(result['counts']['added'],2)
        receipts=result['records'][0]['added_items']
        self.assertEqual({item['label'] for item in receipts},{'第一项任务','第二项任务'})
        workspace=self.call('/workspace')[1]
        with app.connection() as db:
            raw=json.loads(db.execute('SELECT snapshot FROM source_sync_runs WHERE run_id=?',(run['run_id'],)).fetchone()[0])
            for record in raw['records']:
                record.pop('added_items');record.pop('review_items')
            db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?',(json.dumps(raw),run['run_id']))
        app.initialize()
        restored=self.call('/sources/sync-runs/'+run['run_id'])[1]
        self.assertEqual(restored['records'][0]['added_items'],receipts)
        self.assertEqual(self.call('/workspace')[1],workspace)
        # Once saved, receipt names remain a historical snapshot after user edits.
        with app.connection() as db:
            tasks=json.loads(db.execute('SELECT value FROM workspace WHERE user_id=? AND name="tasks"',(self.uid,)).fetchone()[0])
            tasks[0]['title']='用户后来修改的名字'
            db.execute('UPDATE workspace SET value=? WHERE user_id=? AND name="tasks"',(json.dumps(tasks),self.uid))
        app.initialize()
        self.assertEqual(self.call('/sources/sync-runs/'+run['run_id'])[1]['records'][0]['added_items'],receipts)

    def test_recovery_without_tasks_resolves_old_errors_after_quiet_receipt_expires(self):
        request,drafts=example();rid=request['records'][0]['record']['input_id']
        self.drafts[rid]=empty_reply(request['records'][0]['record'])
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=app.ApiError(502,'合成失败')):
            first=self.start();jobs.wait_for_jobs(app.DB)
        old=self.until(first['run_id'],lambda r:r['state']=='failed')
        with patch.object(jobs.teaching,'recognize',side_effect=self.model):
            status,child=self.call('/sources/sync-runs/'+first['run_id']+'/retry',dict(contract_version='source-sync/0.1',operation_id='retry:empty',consent=True))
            self.assertEqual(status,202,child);jobs.wait_for_jobs(app.DB)
        listing=self.call('/sources/sync-runs')[1]
        self.assertEqual(len(listing['runs']),1)
        self.assertTrue(listing['runs'][0]['records'][0]['recovered'])
        check('runs',listing)
        self.assertTrue(self.call('/sources/sync-runs/'+first['run_id'])[1]['records'][0]['recovered'])
        self.assertTrue(all(batch['sourceSyncManaged'] for batch in self.call('/recognition/batches')[1]))
        with app.connection() as db:jobs.prune_quiet(db,now=time.time()+jobs.QUIET_RECEIPT_SECONDS+1)
        self.assertEqual(len(self.call('/sources/sync-runs')[1]['runs']),1)
        self.assertTrue(self.call('/sources/sync-runs/'+first['run_id'])[1]['records'][0]['recovered'])
        self.assertTrue(all(batch['sourceSyncManaged'] for batch in self.call('/recognition/batches')[1]))
        self.assertEqual(self.call('/sources/sync-runs/'+first['run_id']+'/retry',dict(contract_version='source-sync/0.1',operation_id='retry:old',consent=True))[0],409)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the HTTP-to-frontend recovery regression')
    def test_mixed_retry_with_no_tasks_locates_compacted_recovery_through_http_and_frontend(self):
        request,_=example(('explicit-complete',)*2)
        recovered_input=request['records'][0]['record']['input_id']
        self.drafts[recovered_input]=empty_reply(request['records'][0]['record'])
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=app.ApiError(502,'合成模型故障')):
            first=self.start();jobs.wait_for_jobs(app.DB)
        old=self.until(first['run_id'],lambda run:run['state']=='failed')
        recovered_request=next(item['request_id'] for item in old['records'] if item['input_id']==recovered_input)
        def model(request,ApiError):
            if request['records'][0]['record']['input_id']!=recovered_input: raise ApiError(502,'其他来源仍失败')
            return self.model(request,ApiError)
        with patch.object(jobs.teaching,'recognize',side_effect=model):
            status,retry=self.call('/sources/sync-runs/'+first['run_id']+'/retry',dict(contract_version='source-sync/0.1',operation_id='retry:mixed-empty',consent=True))
            self.assertEqual(status,202,retry);jobs.wait_for_jobs(app.DB)
        target=self.until(retry['run_id'],lambda run:run['state']=='partial_success')
        self.assertEqual(target['counts']['failed'],1)
        self.assertEqual(target['counts']['added'],0);self.assertEqual(target['counts']['review'],0)
        self.assertNotIn(recovered_request,[item['request_id'] for item in target['records']])
        restored=self.call('/sources/sync-runs/'+first['run_id'])[1]
        record=next(item for item in restored['records'] if item['request_id']==recovered_request)
        self.assertEqual(record['recovered_by'],dict(run_id=target['run_id'],request_id=recovered_request))
        self.assertTrue(record['recovered'])
        app.initialize()
        jar=next(handler.cookiejar for handler in self.client.handlers if hasattr(handler,'cookiejar'))
        config=dict(base=self.base,cookie='; '.join(cookie.name+'='+cookie.value for cookie in jar),
                    old_run_id=first['run_id'],target_run_id=target['run_id'],request_id=recovered_request)
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([shutil.which('node'),str(root/'frontend/tests/fixtures/locate-compacted-recovery.mjs')],
                              input=json.dumps(config),text=True,capture_output=True,timeout=20,cwd=root)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(self.call('/workspace')[1].get('tasks',{}).get('value'))
        with app.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM source_sync_runs').fetchone()[0],2)

    def test_sync_batch_ownership_does_not_cross_accounts(self):
        request,_=example();record=request['records'][0]['record'];record['body_status']='partial'
        self.drafts[record['input_id']]=empty_reply(record,'incomplete')
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=self.model):
            first=self.start();jobs.wait_for_jobs(app.DB)
        self.assertTrue(self.call('/recognition/batches')[1][0]['sourceSyncManaged'])
        with app.connection() as db:
            db.execute('UPDATE source_sync_runs SET user_id=? WHERE run_id=?',('different-user',first['run_id']))
        self.assertFalse(self.call('/recognition/batches')[1][0]['sourceSyncManaged'])

    def test_normal_sync_with_new_snapshot_resolves_latest_error_for_same_source_only(self):
        def store(db, key, identity, state):
            run=jobs.start(db,self.uid,dict(contract_version='source-sync/0.1',operation_id=key,sources=['teaching_web'],consent=True),app.ApiError)
            run['records']=[dict(request_id=key,input_id=identity,label='相同标题',course=None,state=state,added=0,review=0,
                                 seconds=None,error=jobs.error('agent_timeout','超时',True) if state=='failed' else None)]
            jobs.save(db,run,'failed' if state=='failed' else 'succeeded')
            return run
        with app.connection() as db:
            foreign=store(db,'other-credential','same-source','failed')
            db.execute('UPDATE source_sync_runs SET credential=? WHERE run_id=?',('old-credential',foreign['run_id']))
            other=store(db,'other-source','other-source','failed')
            first=store(db,'old-snapshot','same-source','failed')
            latest=store(db,'latest-failure','same-source','failed')
            success=store(db,'fresh-success','same-source','succeeded')
        listing=self.call('/sources/sync-runs')[1];check('runs',listing)
        self.assertEqual([run['run_id'] for run in listing['runs']],[latest['run_id'],other['run_id'],foreign['run_id']])
        self.assertEqual(listing['runs'][0]['state'],'succeeded')
        self.assertTrue(listing['runs'][0]['records'][0]['recovered'])
        self.assertEqual(listing['runs'][0]['records'][0]['recovered_by'],dict(run_id=success['run_id'],request_id='fresh-success'))
        self.assertFalse(listing['runs'][1]['records'][0].get('recovered',False))
        self.assertFalse(listing['runs'][2]['records'][0].get('recovered',False))
        self.assertTrue(self.call('/sources/sync-runs/'+first['run_id'])[1]['records'][0]['recovered'])
        self.assertEqual(self.call('/sources/sync-runs/'+latest['run_id']+'/retry',dict(contract_version='source-sync/0.1',operation_id='stale-retry',consent=True))[0],409)

    def test_legacy_identity_uses_durable_binding_and_projects_recovery_without_rewriting_old_receipts(self):
        with app.connection() as db:
            def store(key, state, named=False):
                run=jobs.start(db,self.uid,dict(contract_version='source-sync/0.1',operation_id=key,sources=['teaching_web'],consent=True),app.ApiError)
                run['records']=[dict(request_id=key,label='同名的第二章作业',course=None,state=state,added=int(named),review=0,seconds=None,
                                     error=jobs.error('model_configuration','模型配置错误',False) if state=='failed' else None)]
                if named: run['records'][0].update(input_id='source:chapter-two',added_items=[dict(candidate_id='task-candidate',task_id='new-task',label='离散数学第二章作业',course=None)])
                jobs.save(db,run,'failed' if state=='failed' else 'succeeded')
                return run
            old=store('legacy:missing-snapshot','failed')
            db.execute('INSERT INTO teaching_submission_requests VALUES(?,?,?,?)',(self.uid,'campus-account','legacy:missing-snapshot','source:chapter-two'))
            latest=store('legacy:latest-snapshot','failed')
            db.execute('INSERT INTO teaching_submission_requests VALUES(?,?,?,?)',(self.uid,'campus-account','legacy:latest-snapshot','source:chapter-two'))
            unrelated=store('unrelated:same-title','failed')
            db.execute('INSERT INTO teaching_submission_requests VALUES(?,?,?,?)',(self.uid,'campus-account','unrelated:same-title','other-source'))
            successful=store('success:new-snapshot','succeeded',True)
            # Simulate an existing receipt from before recovery links were saved.
            old['records'][0].pop('recovered',None);old['records'][0].pop('recovered_by',None)
            original=json.dumps(old)
            db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?',(original,old['run_id']))
            latest['records'][0].update(recovered=True)
            latest['records'][0].pop('recovered_by',None)
            db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?',(json.dumps(latest),latest['run_id']))
        listing=self.call('/sources/sync-runs')[1];check('runs',listing)
        self.assertEqual([run['run_id'] for run in listing['runs']],[successful['run_id'],unrelated['run_id'],latest['run_id']])
        self.assertFalse(listing['runs'][1]['records'][0].get('recovered',False))
        self.assertEqual(listing['runs'][2]['records'][0]['recovered_by'],dict(run_id=successful['run_id'],request_id='success:new-snapshot'))
        # Detail navigation can still follow a hidden legacy record. Only the
        # recovery metadata is enriched; original errors, counts and state stay.
        details=self.call('/sources/sync-runs/'+old['run_id'])[1];check('run',details)
        self.assertEqual(details['state'],old['state']);self.assertEqual(details['counts'],old['counts'])
        self.assertEqual(details['records'][0]['state'],'failed')
        self.assertEqual(details['records'][0]['error'],old['records'][0]['error'])
        self.assertEqual(details['records'][0]['recovered_by'],dict(run_id=successful['run_id'],request_id='success:new-snapshot'))
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT snapshot FROM source_sync_runs WHERE run_id=?',(old['run_id'],)).fetchone()[0],original)
            self.assertNotIn('recovered_by',jobs.get(db,self.uid,old['run_id'],app.ApiError)['records'][0])

    def test_detail_recovery_follows_legacy_replacement_chain_beyond_history_without_writes(self):
        def item(request_id, state='failed', identity=None):
            value=dict(request_id=request_id,label=request_id,course=None,state=state,added=int(state=='succeeded'),
                       review=0,seconds=None,error=jobs.error('agent_timeout','旧识别错误',True) if state=='failed' else None)
            if identity: value['input_id']=identity
            if state=='succeeded':
                value['added_items']=[dict(candidate_id=request_id,task_id=request_id,label=request_id,course=None)]
            return value
        def store(db, key, records, replacements=None):
            run=jobs.start(db,self.uid,dict(contract_version='source-sync/0.1',operation_id=key,sources=['teaching_web'],consent=True),app.ApiError)
            run['records']=records
            if replacements: run['recovery_replacements']=replacements
            jobs.save(db,run,'failed' if any(record['state']=='failed' for record in records) else 'succeeded')
            return run
        with app.connection() as db:
            old=store(db,'detail:old',[item('legacy:original'),item('legacy:source','failed','stable-source'),
                                      item('other:credential','failed','credential-source'),item('other:user','failed','user-source')])
            original=json.dumps(old)
            store(db,'detail:replacement',[item('legacy:replacement')],{'legacy:replacement':'legacy:original'})
            success=store(db,'detail:success',[item('legacy:success','succeeded'),item('fresh:source','succeeded','stable-source')],
                          {'legacy:success':'legacy:replacement'})
            foreign=store(db,'detail:foreign',[item('foreign:credential','succeeded','credential-source')])
            db.execute('UPDATE source_sync_runs SET credential=? WHERE run_id=?',('different-credential',foreign['run_id']))
            foreign_user=dict(foreign,user_id='another-user',run_id='detail:another-user',operation_id='detail:another-user',
                              records=[item('foreign:user','succeeded','user-source')])
            foreign_user['counts']=jobs.counts(foreign_user)
            db.execute('INSERT INTO source_sync_runs VALUES(?,?,?,?,?,?)',('another-user',foreign_user['run_id'],
                       foreign_user['operation_id'],jobs.credential(db,self.uid),'succeeded',json.dumps(foreign_user)))
            for index in range(21): store(db,'detail:padding:'+str(index),[item('padding:'+str(index),'succeeded')])
            # Simulate receipts written before recovery links were persisted.
            db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?',(original,old['run_id']))
            self.assertNotIn(old['run_id'],[run['run_id'] for run in jobs.history(db,self.uid)])
            writes=db.total_changes
            details=jobs.get_details(db,self.uid,old['run_id'],app.ApiError)
            self.assertEqual(db.total_changes,writes)
            self.assertEqual([record.get('recovered_by') for record in details['records']],
                             [dict(run_id=success['run_id'],request_id='legacy:success'),
                              dict(run_id=success['run_id'],request_id='fresh:source'),None,None])
        fetched=self.call('/sources/sync-runs/'+old['run_id'])[1];check('run',fetched)
        self.assertEqual(fetched,jobs.view(details))
        for record in details['records']:
            record.pop('recovered',None);record.pop('recovered_by',None)
        self.assertEqual(details,old)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT snapshot FROM source_sync_runs WHERE run_id=?',(old['run_id'],)).fetchone()[0],original)

    def test_normal_sync_completed_snapshot_recovers_old_incomplete_source(self):
        request,drafts=example();record=request['records'][0]['record']
        record['body_status']='partial'
        self.drafts[record['input_id']]=empty_reply(record,'incomplete')
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))) as crawler,patch.object(jobs.teaching,'recognize',side_effect=self.model):
            first=self.start();jobs.wait_for_jobs(app.DB)
            old=self.call('/sources/sync-runs/'+first['run_id'])[1]
            self.assertEqual(old['counts']['incomplete'],1)
            record['body_status']='complete';record['text']+='\n正文现已补齐。'
            self.drafts.update(drafts)
            crawler.return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))
            second=self.start('normal:completed');jobs.wait_for_jobs(app.DB)
        latest=self.call('/sources/sync-runs/'+second['run_id'])[1]
        self.assertEqual(latest['counts']['added'],1)
        self.assertNotEqual(old['records'][0]['request_id'],latest['records'][0]['request_id'])
        self.assertTrue(self.call('/sources/sync-runs/'+first['run_id'])[1]['records'][0].get('recovered'))
        self.assertEqual([r['run_id'] for r in self.call('/sources/sync-runs')[1]['runs']],[second['run_id'],first['run_id']])
        app.initialize()
        self.assertEqual([r['run_id'] for r in self.call('/sources/sync-runs')[1]['runs']],[second['run_id'],first['run_id']])

    def test_normal_snapshot_recovery_is_scoped_to_credential_and_source(self):
        def store(db,operation,identity,state):
            run=jobs.start(db,self.uid,dict(contract_version='source-sync/0.1',operation_id=operation,sources=['teaching_web'],consent=True),app.ApiError)
            run['records']=[dict(request_id=operation,input_id=identity,label='相同标题',course=None,state=state,added=0,review=0,seconds=None,
                error=jobs.error('source_incomplete','正文不完整',False) if state=='incomplete' else None)]
            jobs.save(db,run,'failed' if state=='incomplete' else 'succeeded')
            return run
        with app.connection() as db:
            same=store(db,'old:same','source:a','incomplete')
            foreign=store(db,'old:credential','source:a','incomplete')
            db.execute('UPDATE source_sync_runs SET credential=? WHERE run_id=?',('other-credential',foreign['run_id']))
            other=store(db,'old:source','source:b','incomplete')
            store(db,'fresh:success','source:a','succeeded')
        self.assertTrue(self.call('/sources/sync-runs/'+same['run_id'])[1]['records'][0].get('recovered'))
        self.assertFalse(self.call('/sources/sync-runs/'+foreign['run_id'])[1]['records'][0].get('recovered'))
        self.assertFalse(self.call('/sources/sync-runs/'+other['run_id'])[1]['records'][0].get('recovered'))

    def test_history_keeps_latest_retry_failure_without_changing_recovery_receipts(self):
        request,_=example()
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=app.ApiError(502,'合成失败')):
            first=self.start();jobs.wait_for_jobs(app.DB)
            latest=first
            for index in range(3):
                status,latest=self.call('/sources/sync-runs/'+latest['run_id']+'/retry',dict(contract_version='source-sync/0.1',operation_id='repeat:'+str(index),consent=True))
                self.assertEqual(status,202,latest);jobs.wait_for_jobs(app.DB)
                listing=self.call('/sources/sync-runs')[1];check('runs',listing)
                self.assertEqual([run['run_id'] for run in listing['runs']],[latest['run_id']])
            self.assertEqual(self.call('/sources/sync-runs/'+first['run_id'])[1]['counts']['failed'],1)
            app.initialize()
            self.assertEqual([run['run_id'] for run in self.call('/sources/sync-runs')[1]['runs']],[latest['run_id']])

    def test_history_groups_by_source_error_and_credentials_and_preserves_mixed_results(self):
        def failure(rid, identity, code='recognition_failed'):
            return dict(request_id=rid,input_id=identity,label='相同通知标题',course=None,state='failed',added=0,review=0,
                        seconds=None,error=jobs.error(code,'相同错误文案',True))
        def store(db, operation, records, error=None):
            run=jobs.start(db,self.uid,dict(contract_version='source-sync/0.1',operation_id=operation,sources=['teaching_web'],consent=True),app.ApiError)
            run['records']=records;run['error']=error;jobs.save(db,run,'partial_success' if any(item['added'] for item in records) else 'failed')
            return run
        workspace=self.call('/workspace')[1]
        with app.connection() as db:
            success=dict(request_id='success',label='真实新增任务',course=None,state='succeeded',added=1,seconds=None,error=None,
                added_items=[dict(candidate_id='candidate',task_id='task',label='真实新增任务',course=None)])
            mixed=store(db,'mixed',[success,failure('a:old','source:a')])
            other_credential=store(db,'other-credential',[failure('a:account','source:a')])
            db.execute('UPDATE source_sync_runs SET credential=? WHERE run_id=?',('previous-account',other_credential['run_id']))
            other_source=store(db,'other-source',[failure('b','source:b')])
            other_type=store(db,'other-type',[failure('a:type','source:a','attachment_unreadable')])
            for index in range(25):latest=store(db,'duplicate:'+str(index),[failure('a:'+str(index),'source:a')])
            # A different user's newer receipt must not shadow this account.
            foreign=dict(latest,user_id='unrelated-user',run_id='foreign',operation_id='foreign')
            db.execute('INSERT INTO source_sync_runs VALUES(?,?,?,?,?,?)',('unrelated-user','foreign','foreign',jobs.credential(db,self.uid),'failed',json.dumps(foreign)))
        listing=self.call('/sources/sync-runs')[1];check('runs',listing)
        self.assertEqual([run['run_id'] for run in listing['runs']],[latest['run_id'],other_type['run_id'],other_source['run_id'],other_credential['run_id'],mixed['run_id']])
        projected=listing['runs'][-1]
        self.assertEqual(projected['counts']['added'],1);self.assertEqual(projected['counts']['failed'],0)
        self.assertEqual(projected['state'],'succeeded');self.assertEqual(projected['records'][0]['added_items'],success['added_items'])
        self.assertEqual(self.call('/sources/sync-runs/'+mixed['run_id'])[1]['counts']['failed'],1)
        self.assertEqual(self.call('/workspace')[1],workspace)

    def test_recovered_duplicate_with_saved_results_does_not_reappear_as_an_error(self):
        with app.connection() as db:
            runs=[]
            for index in range(2):
                run=jobs.start(db,self.uid,dict(contract_version='source-sync/0.1',operation_id='mixed-recovery:'+str(index),sources=['teaching_web'],consent=True),app.ApiError)
                run['records']=[dict(request_id='partial:'+str(index),input_id='same-source',label='同一来源',course=None,
                                     state='failed',added=1,review=0,seconds=None,error=jobs.error('recognition_failed','部分失败',True),
                                     added_items=[dict(candidate_id='saved:'+str(index),task_id='task:'+str(index),label='已保存任务',course=None)])]
                jobs.save(db,run,'partial_success');runs.append(run)
            recovery=jobs.start(db,self.uid,dict(contract_version='source-sync/0.1',operation_id='mixed-recovery:success',sources=['teaching_web'],consent=True),app.ApiError)
            recovery['records']=[dict(request_id='resolved',input_id='same-source',label='同一来源',course=None,state='succeeded',added=0,review=0,seconds=None,error=None)]
            jobs.save(db,recovery,'succeeded')
        listing=self.call('/sources/sync-runs')[1];check('runs',listing)
        self.assertEqual([run['run_id'] for run in listing['runs']],[runs[1]['run_id'],runs[0]['run_id']])
        self.assertEqual(sum(run['counts']['added'] for run in listing['runs']),2)
        self.assertEqual(sum(bool(item.get('recovered')) for run in listing['runs'] for item in run['records']),1)
        old=listing['runs'][1]['records'][0]
        self.assertEqual(old['state'],'succeeded');self.assertNotIn('recovered_by',old)
        self.assertTrue(self.call('/sources/sync-runs/'+runs[0]['run_id'])[1]['records'][0]['recovered'])

    def test_whole_collection_errors_keep_latest_without_hiding_unrelated_results(self):
        with app.connection() as db:
            ids=[]
            for index in range(3):
                run=jobs.start(db,self.uid,dict(contract_version='source-sync/0.1',operation_id='collection:'+str(index),sources=['teaching_web'],consent=True),app.ApiError)
                run['error']=jobs.error('collection_failed','采集暂时失败 '+str(index),True)
                if index==0:run['updated_tasks']=[dict(task_id='updated',label='实际更新任务',course=None)]
                jobs.save(db,run,'failed');ids.append(run['run_id'])
        listing=self.call('/sources/sync-runs')[1];check('runs',listing)
        self.assertEqual([run['run_id'] for run in listing['runs']],[ids[-1],ids[0]])
        self.assertEqual(listing['runs'][0]['error']['message'],'采集暂时失败 2')
        self.assertEqual(listing['runs'][1]['counts']['updated'],1);self.assertEqual(listing['runs'][1]['state'],'succeeded')
        self.assertEqual(self.call('/sources/sync-runs/'+ids[0])[0],200)

    def test_full_collection_recovers_old_connection_error_even_when_recognition_fails(self):
        request,_=example()
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='')):
            first=self.start('collection:failure');jobs.wait_for_jobs(app.DB)
        old=self.until(first['run_id'],lambda r:r['state']=='failed')
        self.assertEqual(old['records'],[]);self.assertEqual(old['error']['code'],'collection_failed')
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=app.ApiError(502,'合成模型失败')):
            later=self.start('collection:connected');jobs.wait_for_jobs(app.DB)
        raw=self.call('/sources/sync-runs/'+first['run_id'])[1];check('run',raw)
        self.assertEqual(raw['state'],'failed');self.assertEqual(raw['error'],old['error'])
        self.assertEqual(raw['recovered_by'],dict(run_id=later['run_id']))
        listing=self.call('/sources/sync-runs')[1];check('runs',listing)
        projected=next(run for run in listing['runs'] if run['run_id']==first['run_id'])
        self.assertEqual(projected['state'],'succeeded');self.assertEqual(projected['error'],old['error'])
        self.assertEqual(listing['runs'][0]['counts']['failed'],1)
        self.assertNotIn('collection_completed',json.dumps(listing))

    def test_collection_error_recovers_after_credentials_are_resaved_corrected_or_switched(self):
        credentials = [
            ('synthetic-course-user', 'synthetic-password'),
            ('synthetic-course-user', 'corrected-password'),
            ('another-course-user', 'another-password'),
        ]
        for index, (username, password) in enumerate(credentials):
            with self.subTest(username=username, password_changed=index == 1):
                with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='')):
                    first=self.start('credentials-recovery:failed:'+str(index));jobs.wait_for_jobs(app.DB)
                old=self.until(first['run_id'],lambda r:r['state']=='failed')
                self.assertEqual(old['records'],[])
                with app.connection() as db: original_credential=jobs.credential(db,self.uid)
                self.assertEqual(self.call('/sources/credentials',dict(username=username,password=password))[0],200)
                with app.connection() as db: self.assertNotEqual(jobs.credential(db,self.uid),original_credential)
                with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='')):
                    later=self.start('credentials-recovery:connected:'+str(index));jobs.wait_for_jobs(app.DB)
                raw=self.call('/sources/sync-runs/'+first['run_id'])[1];check('run',raw)
                self.assertEqual(raw['recovered_by'],dict(run_id=later['run_id']))
                listing=self.call('/sources/sync-runs')[1];check('runs',listing)
                projected=next(run for run in listing['runs'] if run['run_id']==first['run_id'])
                self.assertEqual(projected['state'],'succeeded')

    def test_quiet_collection_recovery_persists_after_target_expiry_and_restart(self):
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='')):
            first=self.start('quiet-recovery:failed');jobs.wait_for_jobs(app.DB)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='')):
            later=self.start('quiet-recovery:checked');jobs.wait_for_jobs(app.DB)
        listing=self.call('/sources/sync-runs')[1];check('runs',listing)
        self.assertEqual([run['run_id'] for run in listing['runs']],[first['run_id']])
        self.assertEqual(listing['runs'][0]['recovered_by'],dict(run_id=later['run_id']))
        self.assertFalse(listing['last_check']['changed'])
        with app.connection() as db:jobs.prune_quiet(db,now=time.time()+jobs.QUIET_RECEIPT_SECONDS+1)
        app.initialize()
        self.assertEqual(self.call('/sources/sync-runs/'+later['run_id'])[0],404)
        listing=self.call('/sources/sync-runs')[1];check('runs',listing)
        self.assertEqual(listing['runs'][0]['recovered_by'],dict(run_id=later['run_id']))
        self.assertEqual(self.call('/sources/sync-runs/'+first['run_id'])[1]['state'],'failed')

    def test_model_retry_scoped_recovery_and_other_users_do_not_clear_collection_error(self):
        body=dict(contract_version='source-sync/0.1',operation_id='whole:failure',sources=['teaching_web'],consent=True)
        with app.connection() as db:
            first=jobs.start(db,self.uid,body,app.ApiError)
            first['error']=jobs.error('collection_failed','旧连接失败',True);jobs.save(db,first,'failed')
            for scoped in (False,True):
                retry=dict(contract_version='source-sync/0.1',operation_id='retry:'+str(scoped),consent=True)
                later=jobs.start(db,self.uid,retry,app.ApiError,parent=first['run_id'],
                                 recovery_targets={} if scoped else None,trigger='retry')
                later['collection_completed']=True
                jobs.save(db,later,'succeeded')
            # Another app user's completed collection has no effect, even when
            # its teaching credential and source scope match the failed run.
            foreign_user=dict(first,user_id='another-user',run_id='another-user-run',
                              operation_id='another-user-operation',state='succeeded',error=None,
                              collection_completed=True)
            db.execute('INSERT INTO source_sync_runs VALUES(?,?,?,?,?,?)',('another-user',foreign_user['run_id'],foreign_user['operation_id'],jobs.credential(db,self.uid),'succeeded',json.dumps(foreign_user)))
            jobs.recover_collection_errors(db,foreign_user)
        listing=self.call('/sources/sync-runs')[1];check('runs',listing)
        old=next(run for run in listing['runs'] if run['run_id']==first['run_id'])
        self.assertEqual(old['state'],'failed');self.assertNotIn('recovered_by',old)
        self.assertNotIn('recovered_by',self.call('/sources/sync-runs/'+first['run_id'])[1])

    def test_legacy_collection_recovery_backfills_before_quiet_receipt_pruning(self):
        body=dict(contract_version='source-sync/0.1',operation_id='legacy:failed',sources=['teaching_web'],consent=True)
        with app.connection() as db:
            first=jobs.start(db,self.uid,body,app.ApiError)
            first['error']=jobs.error('collection_failed','历史采集失败',True);jobs.save(db,first,'failed')
            # No teaching username can be recovered from this legacy encrypted
            # fingerprint after the user has already corrected their password.
            db.execute('UPDATE source_sync_runs SET credential=? WHERE run_id=?',('obsolete-credential',first['run_id']))
            later=jobs.start(db,self.uid,dict(body,operation_id='legacy:success'),app.ApiError)
            jobs.save(db,later,'succeeded')
            first.pop('recovered_by',None)
            db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?',(json.dumps(first),first['run_id']))
            later.pop('collection_completed')
            db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?',(json.dumps(later),later['run_id']))
            db.execute('UPDATE source_sync_quiet_runs SET expires_at=0 WHERE run_id=?',(later['run_id'],))
            # List reconciliation is read-only and can use quiet legacy successes.
            listing=jobs.history(db,self.uid)
            self.assertEqual(listing[0]['recovered_by'],dict(run_id=later['run_id']))
            self.assertNotIn('recovered_by',jobs.get(db,self.uid,first['run_id'],app.ApiError))
        app.initialize()
        old=self.call('/sources/sync-runs/'+first['run_id'])[1];check('run',old)
        self.assertEqual(old['recovered_by'],dict(run_id=later['run_id']))
        self.assertEqual(self.call('/sources/sync-runs/'+later['run_id'])[0],404)

    def test_collection_recovery_keeps_latest_receipt_and_older_mixed_outputs(self):
        body=dict(contract_version='source-sync/0.1',operation_id='whole:mixed',sources=['teaching_web'],consent=True)
        with app.connection() as db:
            first=jobs.start(db,self.uid,body,app.ApiError)
            first['error']=jobs.error('collection_failed','首次失败',True)
            first['updated_tasks']=[dict(task_id='updated',label='实际更新任务',course=None)]
            jobs.save(db,first,'failed')
            latest=jobs.start(db,self.uid,dict(body,operation_id='whole:latest'),app.ApiError)
            latest['error']=jobs.error('collection_failed','最近失败',True);jobs.save(db,latest,'failed')
            later=jobs.start(db,self.uid,dict(body,operation_id='whole:done'),app.ApiError)
            later['collection_completed']=True;jobs.save(db,later,'succeeded')
        listing=self.call('/sources/sync-runs')[1];check('runs',listing)
        self.assertEqual([run['run_id'] for run in listing['runs']],[latest['run_id'],first['run_id']])
        self.assertEqual(listing['runs'][0]['recovered_by'],dict(run_id=later['run_id']))
        self.assertEqual(listing['runs'][1]['counts']['updated'],1)
        self.assertIsNone(listing['runs'][1]['error']);self.assertNotIn('recovered_by',listing['runs'][1])
        self.assertEqual(self.call('/sources/sync-runs/'+first['run_id'])[1]['recovered_by'],dict(run_id=later['run_id']))

    def test_collection_updates_are_kept_even_when_every_source_is_skipped(self):
        request,_=example()
        task=dict(id='existing',title='合成任务',status='not_started',due=None,plans=[],subtasks=[])
        with app.connection() as db:db.execute('INSERT INTO workspace VALUES(?,?,?,?)',(self.uid,'tasks',json.dumps([task]),1))
        def register(db,uid,*args,**kwargs):
            row=db.execute('SELECT value FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
            tasks=json.loads(row['value']);tasks[0]['status']='completed'
            db.execute('UPDATE workspace SET value=?,version=version+1 WHERE user_id=? AND name="tasks"',(json.dumps(tasks),uid))
            return dict(requests=[],summary=dict(total=1,skipped=1,pending=0))
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'register',side_effect=register):
            run=self.start();jobs.wait_for_jobs(app.DB)
        result=self.until(run['run_id'],lambda r:r['state']=='succeeded')
        self.assertEqual(result['counts']['updated'],1);self.assertEqual(result['counts']['added'],0)
        self.assertEqual(result['updated_tasks'][0]['task_id'],'existing')
        self.assertEqual(result['updated_tasks'][0]['changed_fields'],['status'])
        self.assertEqual(len(self.call('/sources/sync-runs')[1]['runs']),1)
        self.assertTrue(self.call('/sources/sync-runs')[1]['last_check']['changed'])

    def test_upgrade_compacts_old_quiet_history_and_preserves_real_results(self):
        request,drafts=example();self.drafts.update(drafts)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=self.model):
            useful=self.start('old:useful');jobs.wait_for_jobs(app.DB)
            quiet=self.start('old:quiet');jobs.wait_for_jobs(app.DB)
        with app.connection() as db:
            raw=json.loads(db.execute('SELECT snapshot FROM source_sync_runs WHERE run_id=?',(quiet['run_id'],)).fetchone()[0])
            raw['counts'].pop('review');raw['counts'].pop('updated');raw['skipped']=52;raw['counts']['skipped']=52
            db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?',(json.dumps(raw),quiet['run_id']))
            db.execute('DELETE FROM source_sync_quiet_runs')
        app.initialize()
        listing=self.call('/sources/sync-runs')[1]
        self.assertEqual([run['run_id'] for run in listing['runs']],[useful['run_id']])
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),1)

    def test_many_quiet_checks_do_not_displace_real_history(self):
        request,drafts=example();self.drafts.update(drafts)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=self.model):
            useful=self.start('useful:first');jobs.wait_for_jobs(app.DB)
        with app.connection() as db:
            for index in range(25):
                run=jobs.start(db,self.uid,dict(contract_version='source-sync/0.1',operation_id='quiet:'+str(index),sources=['teaching_web'],consent=True),app.ApiError,trigger='scheduled')
                jobs.save(db,run,'succeeded')
        listing=self.call('/sources/sync-runs')[1]
        self.assertEqual([run['run_id'] for run in listing['runs']],[useful['run_id']])
        self.assertFalse(listing['last_check']['changed'])

if __name__=='__main__':unittest.main()
