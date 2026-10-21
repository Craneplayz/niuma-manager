"""1/3/5-account HTTP baseline with temporary data and synthetic crawler/model."""
import hashlib
import argparse
import json
import io
import math
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
from server import operations
from server import operations_probe
import teaching_assets
from ddl_text.tests.docx_cases import package, paragraph
from ddl_text.docx_reader import DOCX_MIME


def synthetic_docx(text):
    # Fix ZIP metadata so 1/3/5 runs and later comparisons use identical bytes.
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package(paragraph(text)))) as source, zipfile.ZipFile(output,'w') as target:
        for name in source.namelist():
            target.writestr(zipfile.ZipInfo(name,(2026,1,1,0,0,0)),source.read(name))
    return output.getvalue()


def measure(accounts,real_model=False,attachments=False):
    fixture=SourceJobsTests('test_background_parallel_results_auto_apply_and_idempotency')
    initialize=app.initialize
    def initialize_test_data():
        # The fixture creates a synthetic crawler directory beside its DB;
        # managed test storage must claim its own initially empty directory.
        app.DB=app.DB.parent/'data'/app.DB.name
        initialize()
    with patch.object(app,'initialize',side_effect=initialize_test_data):fixture.setUp()
    source=None
    samples=[];stop=threading.Event()
    def sample_resources():
        while not stop.is_set():
            samples.append(operations_probe.host_resources(app.DB.parent))
            stop.wait(.4)
    sampler=threading.Thread(target=sample_resources)
    try:
        clients=[fixture.client]
        for index in range(1,accounts):
            fixture.client=build_opener(HTTPCookieProcessor(CookieJar()))
            assert fixture.call('/auth/register',dict(name='Synthetic',email=f'load{index}@example.test',password='Synthetic-password!'))[0]==200
            assert fixture.call('/sources/credentials',dict(username='synthetic-course-user',password='synthetic-password'))[0]==200
            clients.append(fixture.client)
        request,drafts=example(('explicit-complete',))
        attachment=b''
        if attachments:
            record=request['records'][0]['record']
            attachment=synthetic_docx(record['text'])
            record['references'].append(dict(ref_id='capacity-docx',kind='attachment',
                label='synthetic.docx',read_state='unread',text=None))
            class Download(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200);self.send_header('Content-Length',str(len(attachment)))
                    self.end_headers();self.wfile.write(attachment)
                def log_message(self,*args):pass
            source=ThreadingHTTPServer(('127.0.0.1',0),Download)
            threading.Thread(target=source.serve_forever,daemon=True).start()
        fixture.drafts.update(drafts)
        payload=json.dumps(request)
        phases={'crawler':[],'model_and_validation':[]}
        lock=threading.Lock()
        active=peak=0
        recognize=jobs.teaching.recognize
        def crawl(*args,**kwargs):
            nonlocal active,peak
            start=time.perf_counter()
            with lock:active+=1;peak=max(peak,active)
            try:
                time.sleep(.5)
                stderr=''
                if attachments:
                    started=time.perf_counter()
                    with build_opener().open(f'http://127.0.0.1:{source.server_port}/synthetic.docx',timeout=10) as response:
                        data=response.read()
                    stderr=json.dumps(dict(contract_version='producer-observation/0.1',stage='attachment_download',
                        duration_ms=(time.perf_counter()-started)*1000,result_code='ok'))
                    digest=hashlib.sha256(data).hexdigest()
                    folder=Path(kwargs['env']['PKU_ATTACHMENT_DIR'])
                    (folder/(digest+'.bin')).write_bytes(data)
                    (folder/'manifest.json').write_text(json.dumps([dict(input_id=record['input_id'],
                        ref_id='capacity-docx',sha256=digest,mime_type=DOCX_MIME,size_bytes=len(data))]),encoding='utf-8')
                return SimpleNamespace(returncode=0,stdout=payload,stderr=stderr)
            finally:
                with lock:active-=1;phases['crawler'].append((time.perf_counter()-start)*1000)
        def model(request,error,**kwargs):
            start=time.perf_counter()
            if real_model:
                result=recognize(request,error,**kwargs)
            else:
                time.sleep(.25)
                if kwargs.get('files'):
                    teaching_assets.enrich(request,kwargs['files'],RecordClient(fixture.drafts),
                        Settings(timeout_seconds=60,model='synthetic-capacity',endpoint='https://example.invalid'),kwargs['save_cache'])
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
        cpu=time.process_time();start=time.perf_counter();observed_start=time.time()
        sampler.start()
        with patch.object(app.source_sync,'subprocess',SimpleNamespace(run=crawl,TimeoutExpired=subprocess.TimeoutExpired)),patch.object(jobs.teaching,'recognize',side_effect=model):
            with ThreadPoolExecutor(max_workers=accounts) as pool:
                submitted=list(pool.map(lambda client:call(client,'/sources/sync-runs',body),clients))
            workspace=[call(client,'/workspace') for client in clients]
            jobs.wait_for_jobs(app.DB,timeout=jobs.QUEUE_SECONDS+300)
        elapsed=time.perf_counter()-start
        cpu_seconds=time.process_time()-cpu
        stop.set();sampler.join()
        completed=0
        for client,(status,run,_) in zip(clients,submitted):
            if status!=202:continue
            actual=call(client,'/sources/sync-runs/'+run['run_id'])[1]
            assert actual['state']=='succeeded',dict(state=actual['state'],code=(actual.get('error') or {}).get('code'),codes=[(r.get('error') or {}).get('code') for r in actual['records']])
            tasks=call(client,'/workspace')[1]['tasks']['value']
            assert len(tasks)==1
            assert call(client,'/sources/sync-runs',body)[1]['run_id']==run['run_id']
            assert len(call(client,'/workspace')[1]['tasks']['value'])==1
            completed+=1
        p95=lambda values:round(sorted(values)[math.ceil(.95*len(values))-1],2)
        with app.connection() as db:
            observed=operations.summary(app.DB,db,observed_start,time.time()+1)
        try:
            import resource
            rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss=int(rss if platform.system()=='Darwin' else rss*1024)
        except ImportError:rss=None
        disk=shutil.disk_usage(app.DB.parent)
        def maximum(key):
            values=[sample[key] for sample in samples if sample.get(key) is not None]
            return max(values) if values else None
        return dict(accounts=accounts,accepted=sum(status==202 for status,_,_ in submitted),completed=completed,
                    rejected_statuses=[status for status,_,_ in submitted if status!=202],
                    seconds=round(elapsed,3),tasks_per_second=round(completed/elapsed,2),
                    submission_p95_ms=p95([ms for _,_,ms in submitted]),workspace_p95_ms=p95([ms for _,_,ms in workspace]),
                    workspace_available=all(status==200 for status,_,_ in workspace),peak_crawlers=peak,
                    cpu_seconds=round(cpu_seconds,3),phase_ms=phases,
                    observed_stages=observed['stages'],provider_usage=observed['providerUsage'],
                    process_peak_rss_bytes=rss,
                    host_samples=len(samples),host_peak_cpu_percent=maximum('cpuPercent'),
                    host_peak_memory_used_bytes=maximum('memoryUsedBytes'),
                    host_disk_peak_used_bytes=maximum('diskUsedBytes'),
                    disk_available_bytes=disk.free,disk_total_bytes=disk.total,
                    attachment_bytes=len(attachment),attachment_sha256=hashlib.sha256(attachment).hexdigest() if attachment else None,
                    source_bytes=len(payload.encode()),source_sha256=hashlib.sha256(payload.encode()).hexdigest())
    finally:
        stop.set()
        if sampler.ident is not None:sampler.join()
        if source is not None:source.shutdown();source.server_close()
        fixture.tearDown()
        fixture.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-model',action='store_true',help='Explicitly use configured provider with synthetic source; may consume API quota')
    parser.add_argument('--accounts',type=int,choices=(1,3,5),nargs='+',default=(1,3,5))
    parser.add_argument('--attachments',action='store_true',help='Download synthetic DOCX over loopback HTTP and use the real local attachment reader')
    args=parser.parse_args()
    with patch.dict(os.environ,NIUMA_ENVIRONMENT='test',NIUMA_ENABLE_WECHAT='0'):
        with redirect_stdout(sys.stderr):results=[measure(n,args.real_model,args.attachments) for n in args.accounts]
        print(json.dumps(dict(platform=platform.platform(),cpus=os.cpu_count(),
            attachments='loopback HTTP download + real DOCX reader' if args.attachments else 'none',
            external_services='synthetic crawler 500ms + configured real provider; no real crawling' if args.real_model else 'synthetic crawler 500ms + model 250ms; no provider calls',
            results=results),indent=2))
