"""Persistent scheduler with synthetic capture/model and deterministic clock ticks."""
import json
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request
from urllib.error import HTTPError

import app
import source_jobs as jobs
import source_schedule as schedule
import test_teaching as fixture
from contracts.source_sync import check
from ddl_text.tests.test_teaching_pipeline import example


class SourceScheduleTests(unittest.TestCase):
    setUp=fixture.TeachingIntegrationTests.setUp
    call=fixture.TeachingIntegrationTests.call
    model=fixture.TeachingIntegrationTests.model

    def tearDown(self):
        jobs.wait_for_jobs(app.DB)
        fixture.TeachingIntegrationTests.tearDown(self)

    def update(self,enabled=True,revision=0,interval=15,consent=True,sources=None):
        body=dict(contract_version='source-sync/0.1',enabled=enabled,revision=revision,
                  interval_minutes=interval,consent=consent,sources=sources or ['teaching_web'])
        req=Request(self.base+'/sources/sync-schedule',json.dumps(body).encode(),{'Content-Type':'application/json'},method='PUT')
        try:
            with self.client.open(req) as response: return response.status,json.load(response)
        except HTTPError as response: return response.code,json.load(response)

    def test_default_off_and_explicit_consent_bounds_and_source(self):
        with patch.dict(os.environ,NIUMA_ENABLE_SCHEDULED_SYNC='0'):
            state=self.call('/sources/sync-schedule')[1];check('schedule',state)
            self.assertFalse(state['available']);self.assertFalse(state['enabled'])
            self.assertEqual(self.update()[0],409)
        with patch.dict(os.environ,NIUMA_ENABLE_SCHEDULED_SYNC='1'):
            self.assertEqual(self.update(consent=False)[0],400)
            for value in (0,14,10081,True):self.assertEqual(self.update(interval=value)[0],400)
            self.assertEqual(self.update(sources=['wechat'])[0],400)
            status,state=self.update(interval=180);self.assertEqual(status,200,state);check('schedule',state)
            self.assertTrue(state['enabled']);self.assertEqual(state['interval_minutes'],180)
            self.assertEqual(self.update()[0],409)
        with patch.dict(os.environ,NIUMA_ENABLE_SCHEDULED_SYNC='0'),patch.object(jobs,'launch') as launch:
            schedule.tick(app.DB,app.ApiError,now=state['next_run_at']+1);launch.assert_not_called()

    def test_due_tick_survives_restart_runs_once_and_saves_real_result(self):
        request,drafts=example(('explicit-complete',));self.drafts.update(drafts)
        with patch.dict(os.environ,NIUMA_ENABLE_SCHEDULED_SYNC='1'):
            state=self.update()[1];app.initialize()
            with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))) as crawl,patch.object(jobs.teaching,'recognize',side_effect=self.model):
                schedule.tick(app.DB,app.ApiError,now=state['next_run_at']-1);self.assertEqual(crawl.call_count,0)
                schedule.tick(app.DB,app.ApiError,now=state['next_run_at']);jobs.wait_for_jobs(app.DB)
                schedule.tick(app.DB,app.ApiError,now=state['next_run_at']);jobs.wait_for_jobs(app.DB)
                self.assertEqual(crawl.call_count,1)
        runs=self.call('/sources/sync-runs')[1]['runs'];self.assertEqual(len(runs),1)
        self.assertEqual(runs[0]['trigger'],'scheduled');self.assertEqual(runs[0]['state'],'succeeded')
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),1)

    def test_concurrent_ticks_manual_overlap_and_capacity_backoff(self):
        with patch.dict(os.environ,NIUMA_ENABLE_SCHEDULED_SYNC='1'),patch.object(jobs,'launch') as launch:
            state=self.update()[1];due=state['next_run_at']
            threads=[threading.Thread(target=schedule.tick,args=(app.DB,app.ApiError,due)) for _ in range(2)]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
            self.assertEqual(launch.call_count,1)
            # An active run is not overlapped, even at a much later due time.
            schedule.tick(app.DB,app.ApiError,now=due+10000);self.assertEqual(launch.call_count,1)
            run=self.call('/sources/sync-runs')[1]['runs'][0]
            self.call('/sources/sync-runs/'+run['run_id']+'/stop',dict(contract_version='source-sync/0.1'))
            future=self.call('/sources/sync-schedule')[1]['next_run_at']
            with patch.object(jobs,'start',side_effect=app.ApiError(503,'full')):
                schedule.tick(app.DB,app.ApiError,now=future)
            self.assertEqual(self.call('/sources/sync-schedule')[1]['next_run_at'],future+60)
            # Existing manual job is also reused/postponed instead of a scheduled duplicate.
            self.call('/sources/sync-runs',dict(contract_version='source-sync/0.1',operation_id='manual:one',sources=['teaching_web'],consent=True))
            schedule.tick(app.DB,app.ApiError,now=future+60)
            self.assertEqual(len(self.call('/sources/sync-runs')[1]['runs']),2)

    def test_credential_change_pauses_and_other_account_has_own_settings(self):
        with patch.dict(os.environ,NIUMA_ENABLE_SCHEDULED_SYNC='1'),patch.object(jobs,'launch') as launch:
            state=self.update()[1]
            self.call('/sources/credentials',dict(username='other-campus',password='synthetic-password'))
            schedule.tick(app.DB,app.ApiError,now=state['next_run_at']);launch.assert_not_called()
            current=self.call('/sources/sync-schedule')[1]
            self.assertFalse(current['enabled']);self.assertEqual(current['error']['code'],'credentials_changed')
            self.call('/auth/register',dict(name='Other',email='schedule-other@example.test',password='Synthetic-password!'))
            other=self.call('/sources/sync-schedule')[1]
            self.assertEqual(other['revision'],0);self.assertIsNone(other['error'])
            self.assertEqual(self.update()[0],400)

    def test_disable_stops_scheduled_call_and_rejects_late_model_write(self):
        request,drafts=example(('explicit-complete',));self.drafts.update(drafts)
        gate=threading.Event();entered=threading.Event()
        def model(request,ApiError):
            entered.set();gate.wait(5);return self.model(request,ApiError)
        with patch.dict(os.environ,NIUMA_ENABLE_SCHEDULED_SYNC='1'),patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))),patch.object(jobs.teaching,'recognize',side_effect=model):
            state=self.update()[1];schedule.tick(app.DB,app.ApiError,now=state['next_run_at'])
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.update(enabled=False,revision=1,consent=False)[0],200)
            gate.set();jobs.wait_for_jobs(app.DB)
        self.assertFalse(self.call('/recognition/batches')[1]);self.assertFalse(self.call('/sources/sync-schedule')[1]['enabled'])
        self.assertEqual(self.call('/sources/sync-runs')[1]['runs'][0]['state'],'stopped')


if __name__=='__main__':unittest.main()
