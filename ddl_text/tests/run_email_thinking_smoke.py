"""Opt-in Qiniu A/B check on synthetic email extraction; no mailbox or app writes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time

from ddl_text.adapters import AgentError
from ddl_text.chat_client import ChatClient
from ddl_text.config import Settings
from ddl_text.contracts import normalize_record
from ddl_text.extraction import ExtractionAdapter
from server.diagnose_email_response import TEST_MODEL, test_options

CASES = [
    ('reply_deadline','请于2026年10月25日17:00前回复本邮件，确认是否参加校园歌手比赛。', [('task','2026-10-25T17:00:00+08:00')]),
    ('room_notice','本周课程上课地点调整为理教408，原上课时间不变，无需回复。', [('notice',None)]),
    ('mixed','请于2026年10月25日17:00前提交实验报告。另：本学期期末考试地点调整为理教408，考试时间仍以原安排为准。', [('task','2026-10-25T17:00:00+08:00'),('notice',None)]),
    ('spam_summary','系统反垃圾日报：今天拦截1封异常邮件，已自动移入垃圾箱。本邮件仅作系统统计，无需操作。', []),
]


def record_for(name,text):
    return normalize_record(dict(input_id='synthetic:'+name,text=text,text_quality='complete',platform='email',
        published_at=dict(value='2026-10-20T10:00:00+08:00',precision='datetime',basis='source'),
        collected_at='2026-10-20T10:01:00+08:00',timezone='Asia/Shanghai',timezone_basis='source',
        source_url=None,source_label='合成邮件诊断',source_item_id=name,course_or_project=None,context=[],upstream_hints=None),'0.2')


def run_case(options, case, mode):
    name,text,expected=case
    responses=[];started=time.monotonic()
    row=dict(case=name,thinking_mode=mode or 'provider_default',expected=expected)
    settings=Settings(mode='real',model=options['model'],endpoint=options['endpoint'],timeout_seconds=90,max_retries=0,thinking_mode=mode)
    try:
        tasks,metrics=ExtractionAdapter(ChatClient(options['key'],responses.append)).extract_with_metrics(record_for(name,text),settings,0)
        actual=[(t.get('content_type','task'),t['deadline']['value']) for t in tasks]
        row.update(status='success' if tasks else 'no_tasks',passed=sorted(actual,key=str)==sorted(expected,key=str),actual=actual,
                   candidates=[dict(title=t['title'],content_type=t.get('content_type'),deadline=t['deadline']) for t in tasks])
    except AgentError as error:
        row.update(status='failed',passed=False,error_code=error.code,diagnostic=error.diagnostic)
    except Exception:
        row.update(status='diagnostic_failed',passed=False)
    row.update(seconds=round(time.monotonic()-started,2),provider_response=responses)
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live',action='store_true');p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    if not args.live:p.error('--live is required for eight synthetic model calls')
    options=test_options(TEST_MODEL)
    fd=os.open(args.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    report=dict(synthetic_only=True,imap_access=False,writes_application_data=False,model=options['model'],max_tokens=8192,cases=[])
    with os.fdopen(fd,'w',encoding='utf-8') as output,ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run_case,options,case,mode) for case in CASES for mode in (None,'disabled')]
        for future in as_completed(futures):
            row=future.result();report['cases'].append(row)
            output.seek(0);json.dump(report,output,ensure_ascii=False,indent=2);output.truncate();output.flush()
            print(json.dumps({k:row[k] for k in ('case','thinking_mode','status','passed','seconds','provider_response')},ensure_ascii=False),flush=True)
    return 0 if all(r['passed'] for r in report['cases'] if r['thinking_mode']=='disabled') else 1


if __name__=='__main__':raise SystemExit(main())
