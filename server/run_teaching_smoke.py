"""Opt-in real API smoke with synthetic HTML, isolated DB and real application routes."""
import argparse
import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from http.cookiejar import CookieJar
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError

import app
import teaching
import reprocessing


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--reprocess',action='store_true',help='also reprocess saved results through HTTP and verify task creation')
    args=parser.parse_args()
    if args.output.exists(): parser.error('use a new output file')
    config=teaching.live_options(args.env_file or teaching.ROOT/'.env')
    root=Path(__file__).resolve().parents[1]
    export=subprocess.run(['node','--import','tsx','tests/export-teaching-smoke.ts'],cwd=root/'crawler',capture_output=True,text=True,encoding='utf-8',check=True)
    requests=json.loads(export.stdout)
    for request in requests:teaching.check_request(request)
    with patch.object(teaching,'live_options',return_value=config),tempfile.TemporaryDirectory(prefix='niuma-teaching-smoke-') as folder:
        app.DB=Path(folder)/'test.sqlite3';app.initialize()
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        client=build_opener(HTTPCookieProcessor(CookieJar()))
        def call(path,body=None):
            req=Request(f'http://127.0.0.1:{server.server_port}/api'+path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json'})
            try:
                with client.open(req,timeout=120) as response:return json.load(response)
            except HTTPError as response:
                raise RuntimeError(f'{path}: {response.code}: {response.read().decode()}') from None
        try:
            call('/auth/register',dict(name='合成验收',email='smoke@example.test',password='Synthetic-Smoke-2026!'))
            call('/sources/credentials',dict(username='synthetic-source',password='synthetic-only'))
            with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='\n'.join(json.dumps(r) for r in requests))):
                pending=call('/sources/sync',dict(sources=['teaching_web']))
            report=dict(synthetic=True,crawler='synthetic HTML through production parsers; campus network not exercised',cases=[])
            expected=['task','task','task','task','candidate']
            for index,view in enumerate(pending['requests']):
                start=time.monotonic()
                batches=call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))
                batch=next(b for b in batches if b['request']['request_id']==view['request_id'])
                for item in batch['items']:
                    if not args.reprocess and not item['candidate']['needs_review']:
                        call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=item['version']))
                with app.connection() as db:
                    row=db.execute('SELECT request,result FROM recognition_batches WHERE request_id=?',(view['request_id'],)).fetchone()
                native=json.loads(row['result']);decisions=[d for r in native['record_results'] for d in r['decisions']]
                actual=[d['recommendation'] for d in decisions]
                expected_dates=['2026-09-29T09:00:00+08:00','2026-10-25T23:59:00+08:00','2026-10-23','2026-10-23',None]
                actual_dates=[d['pair']['assessment']['deadline']['value'] for d in decisions]
                case=dict(input=json.loads(row['request']),output=native,cards=batch['items'],elapsed_seconds=round(time.monotonic()-start,2),expected=expected[index],passed=actual==[expected[index]] and actual_dates==[expected_dates[index]])
                report['cases'].append(case)
                args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(dict(index=index+1,seconds=case['elapsed_seconds'],status=native['status'],actual=actual,passed=case['passed']),ensure_ascii=False),flush=True)
            if args.reprocess:
                directory=call('/recognition/reprocessing/sources')['accounts']
                source=next(a for a in directory if a['label']=='当前教学网账号')
                run=call('/recognition/reprocessing/runs',dict(contract_version='reprocessing/0.1',operation_id='synthetic-reprocess',
                    source_account_id=source['source_account_id'],input_ids=[r['input_id'] for r in source['records']]))
                deadline=time.monotonic()+600
                while run['state'] not in reprocessing.contract.FINAL and time.monotonic()<deadline:
                    time.sleep(.25);run=call('/recognition/reprocessing/runs/'+run['run_id'])
                report['reprocessing']=run
                with app.connection() as db:
                    report['reprocessed_results']=[dict(input=json.loads(row['request']),output=json.loads(row['result']))
                        for row in db.execute("SELECT request,result FROM recognition_batches WHERE request_id LIKE 'reprocessing:%'")]
                report['final_cards']=call('/recognition/batches')
            report['workspace']=call('/workspace');report['passed']=all(c['passed'] for c in report['cases'])
            if args.reprocess:
                tasks=report['workspace'].get('tasks',{}).get('value',[])
                report['passed']=report['passed'] and run['state']=='succeeded' and len(tasks)==4
                report['passed']=report['passed'] and sum(i['state']=='pending' for b in report['final_cards'] for i in b['items'])==1
                print(json.dumps(dict(reprocessing=run['state'],tasks=len(tasks),passed=report['passed'])),flush=True)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            return 0 if report['passed'] else 1
        finally:
            reprocessing.wait_for_jobs(app.DB)
            server.shutdown();server.server_close();worker.join()


if __name__=='__main__':raise SystemExit(main())
