"""Opt-in synthetic file -> crawler -> HTTP sync -> real model -> workspace acceptance."""
import argparse
import base64
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from http.cookiejar import CookieJar
from urllib.request import Request, build_opener, HTTPCookieProcessor
from unittest.mock import patch

import app
from auth_test_support import registration_payload
import teaching
from ddl_text.tests.docx_cases import package, paragraph
from ddl_text.tests.pdf_cases import pdf_bytes
from ddl_text.docx_reader import DOCX_MIME

TEXT='Submit the synthetic experiment report by 2026-11-17 23:30 (Asia/Shanghai).'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('use a new output file')
    config=teaching.live_options(args.env_file)
    from PIL import Image, ImageDraw, ImageFont
    picture=Image.new('RGB',(1400,240),'white');draw=ImageDraw.Draw(picture)
    font=ImageFont.load_default(size=30)
    draw.text((35,35),'Synthetic assignment: submit the experiment report.',fill='black',font=font)
    draw.text((35,100),'Deadline: 2026-11-17 23:30 (Asia/Shanghai).',fill='black',font=font)
    png=io.BytesIO();picture.save(png,format='PNG')
    scan=io.BytesIO();picture.save(scan,format='PDF')
    files=[('text.pdf','application/pdf',pdf_bytes((TEXT,))),('scan.pdf','application/pdf',scan.getvalue()),
        ('image.png','image/png',png.getvalue()),('document.docx',DOCX_MIME,package(paragraph(TEXT)))]
    payload=[dict(name=n,mime=m,data=base64.b64encode(d).decode()) for n,m,d in files]
    subprocess_run=subprocess.run
    report=dict(synthetic=True,scope='Synthetic HTML and files; actual campus authentication/download not exercised.',cases=[])
    with tempfile.TemporaryDirectory(prefix='niuma-teaching-files-smoke-') as folder,patch.object(teaching,'live_options',return_value=config):
        old_db=app.DB;app.DB=Path(folder)/'smoke.sqlite3';app.initialize()
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        client=build_opener(HTTPCookieProcessor(CookieJar()))
        def call(path,body=None):
            req=Request(f'http://127.0.0.1:{server.server_port}/api'+path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json'})
            with client.open(req,timeout=150) as response:return json.load(response)
        def capture(_command,**kwargs):
            return subprocess_run(['node','--import','tsx','tests/export-teaching-attachment-smoke.ts'],
                cwd=teaching.ROOT/'crawler',input=json.dumps(dict(folder=kwargs['env']['PKU_ATTACHMENT_DIR'],files=payload)),
                capture_output=True,text=True,encoding='utf-8',timeout=90)
        try:
            account=registration_payload(dict(name='Synthetic attachments',email='attachment-smoke@example.test',password='Synthetic-Smoke-2026!'),
                                         lambda body:(200,call('/auth/email-code',body)))
            call('/auth/register',account)
            call('/sources/credentials',dict(username='synthetic-user',password='synthetic-only'))
            with patch.object(app.source_sync.subprocess,'run',side_effect=capture):
                pending=call('/sources/sync',dict(sources=['teaching_web']))
            for (name,_,_),view in zip(files,pending['requests']):
                start=time.monotonic()
                batches=call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))
                batch=next(b for b in batches if b['request']['request_id']==view['request_id'])
                cards=batch['items']
                for item in cards:
                    if not item['candidate']['needs_review']:
                        call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=item['version']))
                with app.connection() as db:row=db.execute('SELECT request,result FROM recognition_batches WHERE request_id=?',(view['request_id'],)).fetchone()
                passed=len(cards)==1 and not cards[0]['candidate']['needs_review'] and cards[0]['candidate']['deadline']['value']=='2026-11-17T23:30:00+08:00'
                report['cases'].append(dict(file=name,passed=passed,seconds=round(time.monotonic()-start,2),input=json.loads(row['request']),output=json.loads(row['result'])))
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(dict(file=name,passed=passed,status=batch['result']['status'],seconds=report['cases'][-1]['seconds'])),flush=True)
            report['workspace']=call('/workspace');report['passed']=all(c['passed'] for c in report['cases']) and len(report['workspace'].get('tasks',{}).get('value',[]))==4
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            return 0 if report['passed'] else 1
        finally:
            server.shutdown();server.server_close();thread.join();app.DB=old_db


if __name__=='__main__':raise SystemExit(main())
