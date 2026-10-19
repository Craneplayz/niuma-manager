"""1/3/5-account HTTP baseline with temporary data and synthetic crawler/model."""
import hashlib
import json
import math
import os
import platform
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import source_jobs as jobs
from test_source_jobs import SourceJobsTests
from ddl_text.tests.test_teaching_pipeline import example
from ddl_text.tests.test_teaching_pipeline import RecordClient
from ddl_text.teaching_pipeline import process_teaching_request
from ddl_text.config import Settings
from contracts.teaching_io import snapshot_digest


def measure(accounts):
    fixture=SourceJobsTests('test_background_parallel_results_auto_apply_and_idempotency')
    initialize=app.initialize
    def initialize_test_data():
        # The fixture creates a synthetic crawler directory beside its DB;
        # managed test storage must claim its own initially empty directory.
        app.DB=app.DB.parent/'data'/app.DB.name
        initialize()
    with patch.object(app,'initialize',side_effect=initialize_test_data):fixture.setUp()
    try:
        clients=[fixture.client]
        for index in range(1,accounts):
            fixture.client=build_opener(HTTPCookieProcessor(CookieJar()))
            assert fixture.call('/auth/register',dict(name='Synthetic',email=f'load{index}@example.test',password='Synthetic-password!'))[0]==200
            assert fixture.call('/sources/credentials',dict(username='synthetic-course-user',password='synthetic-password'))[0]==200
            clients.append(fixture.client)
        request,drafts=example(('explicit-complete',))
        fixture.drafts.update(drafts)
        payload=json.dumps(request)
        phases={'crawler':[],'model_and_validation':[]}
        lock=threading.Lock()
        active=peak=0
        def crawl(*args,**kwargs):
            nonlocal active,peak
            start=time.perf_counter()
            with lock:active+=1;peak=max(peak,active)
            try:
                time.sleep(.5)
                return SimpleNamespace(returncode=0,stdout=payload)
            finally:
                with lock:active-=1;phases['crawler'].append((time.perf_counter()-start)*1000)
        def model(request,error):
            start=time.perf_counter()
            time.sleep(.25)
            output=process_teaching_request(request,client=RecordClient(fixture.drafts),settings=Settings(timeout_seconds=60),
                verified_snapshot_digests={snapshot_digest(entry) for entry in request['records']})
            output['policy_version']='teaching-policy-v1:'+'0'*64
            result=output,dict(version=output['policy_version'])
            with lock:phases['model_and_validation'].append((time.perf_counter()-start)*1000)
            return result
        def call(client,path,body=None):
            started=time.perf_counter()
            req=Request(fixture.base+path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json'})
            try:response=client.open(req,timeout=15)
            except HTTPError as error:response=error
            with response:return response.status,json.load(response),(time.perf_counter()-started)*1000
        body=dict(contract_version='source-sync/0.1',sources=['teaching_web'],consent=True,operation_id='baseline')
        cpu=time.process_time();start=time.perf_counter()
        with patch.object(app.source_sync.subprocess,'run',side_effect=crawl),patch.object(jobs.teaching,'recognize',side_effect=model):
            with ThreadPoolExecutor(max_workers=accounts) as pool:
                submitted=list(pool.map(lambda client:call(client,'/sources/sync-runs',body),clients))
            workspace=[call(client,'/workspace') for client in clients]
            jobs.wait_for_jobs(app.DB)
        elapsed=time.perf_counter()-start
        cpu_seconds=time.process_time()-cpu
        completed=0
        for client,(status,run,_) in zip(clients,submitted):
            if status!=202:continue
            actual=call(client,'/sources/sync-runs/'+run['run_id'])[1]
            assert actual['state']=='succeeded',actual['state']
            tasks=call(client,'/workspace')[1]['tasks']['value']
            assert len(tasks)==1
            assert call(client,'/sources/sync-runs',body)[1]['run_id']==run['run_id']
            assert len(call(client,'/workspace')[1]['tasks']['value'])==1
            completed+=1
        p95=lambda values:round(sorted(values)[math.ceil(.95*len(values))-1],2)
        return dict(accounts=accounts,accepted=sum(status==202 for status,_,_ in submitted),completed=completed,
                    rejected_statuses=[status for status,_,_ in submitted if status!=202],
                    seconds=round(elapsed,3),tasks_per_second=round(completed/elapsed,2),
                    submission_p95_ms=p95([ms for _,_,ms in submitted]),workspace_p95_ms=p95([ms for _,_,ms in workspace]),
                    workspace_available=all(status==200 for status,_,_ in workspace),peak_crawlers=peak,
                    cpu_seconds=round(cpu_seconds,3),phase_ms=phases,
                    source_bytes=len(payload.encode()),source_sha256=hashlib.sha256(payload.encode()).hexdigest())
    finally:
        fixture.tearDown()
        fixture.doCleanups()


if __name__=='__main__':
    with patch.dict(os.environ,NIUMA_ENVIRONMENT='test',NIUMA_ENABLE_WECHAT='0'):
        print(json.dumps(dict(platform=platform.platform(),cpus=os.cpu_count(),
            external_services='synthetic crawler 500ms + model 250ms; no attachments or provider calls',
            results=[measure(n) for n in (1,3,5)]),indent=2))
