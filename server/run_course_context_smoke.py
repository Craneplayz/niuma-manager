"""Synthetic DOCX -> authenticated HTTP -> real model -> saved task acceptance.

Requires --live; creates and closes one temporary API and database. Never reads
personal coursework, invokes a crawler or writes to a deployed environment.
"""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import app
import teaching
import test_teaching as harness
from server.test_course_context import request
from ddl_text.codex_client import live_options
from ddl_text.tests.docx_cases import package,paragraph
from ddl_text.docx_reader import DOCX_MIME


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true');parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if not args.live or args.output.exists():parser.error('require --live and a new report path')
    options=live_options(args.env_file)
    if options['provider']!='qiniu':parser.error('requires configured Qiniu provider')
    report=dict(synthetic_only=True,model=options['model'],passed=False)
    fixture=harness.TeachingIntegrationTests()
    fixture.setUp()
    try:
        primary=request('assignment','第11讲书面作业：完成习题1至5。截止日期按课程大纲和课程更正安排。')
        sources=[primary];assets={}
        for name,heading,body in [
            ('syllabus','教学大纲','课程大纲：本学期所有书面作业统一于2026年11月6日提交。'),
            ('correction','课程安排更正','课程安排更正：第11讲书面作业不适用课程大纲中的统一日期，改为2026年11月13日提交。其他作业不变。')]:
            material=request(name,heading+'，详见附件。')
            material['records'][0]['record']['references']=[dict(ref_id='file:1',kind='attachment',label=name+'.docx',read_state='unread',text=None)]
            data=package(paragraph(body));sources.append(material)
            assets[name]=[dict(input_id=name,ref_id='file:1',data=data,mime_type=DOCX_MIME,sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data))]
        with app.connection() as db:
            pending=teaching.register(db,fixture.uid,'synthetic-course-user',sources,assets=assets)
        rid=next(v['request_id'] for v in pending['requests'] if v['records'][0]['input_id']=='assignment')
        with patch.object(teaching,'live_options',return_value=options):
            status,batches=fixture.call('/recognition/batches',dict(contract_version=teaching.RUN,request_id=rid))
        report['http_status']=status
        if status!=200:report['response']=batches;return 1
        batch=next(b for b in batches if b['request']['request_id']==rid);report['batch']=batch
        if len(batch['items'])!=1: return 1
        item=batch['items'][0];card=item['candidate']
        context_status,context=fixture.call('/recognition/course-context',dict(request_id=rid));report['context']=context
        file_passages=[d for r in context.get('records',[]) for d in r['passages'] if d['part_id']=='file:1']
        if not (card['deadline']['value']=='2026-11-13' and not card['needs_review'] and context_status==200 and len(file_passages)==2):return 1
        status,_=fixture.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))
        report['save_status']=status
        with app.connection() as db:
            again=teaching.register(db,fixture.uid,'synthetic-course-user',sources,assets=assets)
            report['attachment_cache']=[json.loads(row[0])['read_state'] for row in db.execute('SELECT result FROM teaching_asset_files WHERE user_id=?',(fixture.uid,))]
        report['repeated_assignment']=any(v['records'][0]['input_id']=='assignment' for v in again['requests'])
        tasks=fixture.call('/workspace')[1]['tasks']['value'];report['tasks']=tasks
        report['passed']=(status==200 and len(tasks)==1
                          and tasks[0]['due']==dict(date='2026-11-13',precision='date')
                          and not report['repeated_assignment'] and report['attachment_cache']==['complete','complete'])
        return 0 if report['passed'] else 1
    finally:
        fixture.tearDown();fixture.doCleanups()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(passed=report['passed'],output=str(args.output))),flush=True)


if __name__=='__main__':raise SystemExit(main())
