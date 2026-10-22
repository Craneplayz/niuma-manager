"""Opt-in real model smoke using only fixed synthetic input, not an accuracy benchmark."""
import argparse
import copy
import hashlib
import io
import json
import subprocess
import time
from pathlib import Path

from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.extraction import ExtractionAdapter
from ddl_text.pipeline import process_request
from ddl_text.provider import make_client

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    ('relative', '明天上ics'),
    ('personal', '周五去图书馆取预约书'),
    ('no-deadline', '完成第四章书面作业，做第1、3题。'),
    ('independent', '请在2026年10月25日提交第四章书面作业，并在2026年10月27日提交第四章编程作业。两项作业分别提交。'),
    ('reference', '这里是本周课件，仅供自愿复习，无需提交或完成任何任务。'),
    ('completed', '上周已经参加了研讨会，报告也交完了。'),
    ('declined', '社团本周举办摄影讲座，我不参加。'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.live: parser.error('--live is required; this uses the configured paid model')
    if args.output.exists(): parser.error('output already exists')
    options = live_options(args.env_file)
    settings = Settings(mode='real', model=options['model'], endpoint=options['endpoint'],
        timeout_seconds=60, max_retries=0, thinking_mode='disabled' if options['model'].startswith('deepseek/') else None)
    template = json.loads((ROOT / 'contracts/source-to-text/text-frontend-v02-request.json').read_text(encoding='utf-8'))
    report = dict(synthetic_only=True, provider=options['provider'], model=options['model'],
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)),
        scope='Real provider smoke, not deployed-environment or statistical accuracy validation', cases=[])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    def save(row):
        report['cases'].append(row)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({key:row[key] for key in ('case','passed','seconds')},ensure_ascii=False),flush=True)
    for name, text in CASES:
        record = copy.deepcopy(template['records'][0])
        record.update(input_id='synthetic:'+name, text=text, platform='manual', course_or_project=None,
            source_label='合成验证', context=[], published_at=dict(value='2026-10-22T10:00:00+08:00',precision='datetime',basis='source'))
        request = dict(contract_version='0.2',request_id='synthetic-live:'+name,records=[record])
        events=[]; started=time.monotonic()
        result=process_request(request,ExtractionAdapter(make_client(options)),settings,budget_seconds=65,
            diagnostic_event=lambda stage,**fields:events.append(dict(stage=stage,**fields)))
        tasks=result.get('tasks',[])
        passed=(result['status']=='no_tasks' if name in ('reference','completed','declined') else
                result['status']=='success' and len(tasks)>=(2 if name=='independent' else 1))
        if name=='no-deadline': passed=passed and all(task['deadline']['value'] is None for task in tasks)
        save(dict(case=name,input=text,passed=passed,seconds=round(time.monotonic()-started,2),result=result,events=events))
    if options['provider']=='qiniu':
        from PIL import Image, ImageDraw, ImageFont
        from ddl_text.chat_client import ChatClient
        from ddl_text.media_pipeline import process_media_request, MediaLimits
        with Image.new('RGB',(1200,300),'white') as picture:
            ImageDraw.Draw(picture).text((40,60),'Submit the lab report by October 25, 2026.',
                fill='black',font=ImageFont.load_default(size=34))
            stream=io.BytesIO();picture.save(stream,format='PNG');data=stream.getvalue()
        request=json.loads((ROOT/'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        request['request_id']='synthetic-live:image'
        record=request['records'][0]
        record.update(context=[],platform='unknown',course_or_project=None,source_label='Synthetic smoke image')
        record['attachments'][0].update(size_bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        events=[];started=time.monotonic()
        result=process_media_request(request,lambda asset,**kwargs:data,ChatClient(options['key']),settings,
            MediaLimits(record_seconds=65,batch_seconds=70),
            diagnostic_event=lambda stage,**fields:events.append(dict(stage=stage,**fields)))
        tasks=result.get('tasks',[])
        passed=(result['status']=='success' and bool(tasks) and all(task['needs_review'] for task in tasks)
                and any((task['deadline']['value'] or '').startswith('2026-10-25') for task in tasks))
        save(dict(case='image',passed=passed,seconds=round(time.monotonic()-started,2),result=result,events=events))
    else:
        report['image_skipped']='Configured provider does not support this image adapter'
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(case['passed'] for case in report['cases']) else 1


if __name__ == '__main__': raise SystemExit(main())
