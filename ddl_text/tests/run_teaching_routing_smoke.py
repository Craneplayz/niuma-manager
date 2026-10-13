"""Opt-in synthetic model classification smoke through the native teaching pipeline."""
import argparse
import json
import time
from pathlib import Path

from contracts.teaching_io import snapshot_digest
from ddl_text.chat_client import ChatClient
from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.teaching_pipeline import process_teaching_request
from ddl_text.tests.teaching_cases import routing_cases


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--env-file',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--cases',nargs='+',choices=[c['name'] for c in routing_cases()])
    args=parser.parse_args()
    if not args.live or args.output.exists():parser.error('require --live and a new output file')
    options=live_options(args.env_file)
    if options['provider']!='qiniu':parser.error('requires configured Qiniu provider')
    settings=Settings(mode='real',model=options['model'],endpoint=options['endpoint'],timeout_seconds=60,
        max_retries=0,thinking_mode='disabled' if options['model'].startswith('deepseek/') else None)
    client=ChatClient(options['key'])
    report=dict(synthetic_only=True,scope='Native model call and validation; no production writes',cases=[])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:json.dump(report,f)
    for case in routing_cases():
        if args.cases and case['name'] not in args.cases:continue
        entry=dict(record=case['record'],source=dict(label='合成教学网通知',url=None,course_or_project='算法导论'))
        request=dict(contract_version='0.4',request_id='smoke:'+case['name'],records=[entry])
        started=time.monotonic()
        result=process_teaching_request(request,client=client,settings=settings,
            verified_snapshot_digests={snapshot_digest(entry)},budget_seconds=65)
        row=result['record_results'][0];expected=case['expected'];decisions=row['decisions']
        if 'content_types' in expected:
            passed=(sorted(d.get('content_type','') for d in decisions)==sorted(expected['content_types'])
                and all(d['recommendation']==expected['recommendation'] and d['pair']['assessment']['deadline']['value'] is None for d in decisions))
        elif expected['recommendation']=='no_tasks':
            passed=row['status']=='no_tasks' or bool(decisions) and all(d['recommendation']=='filtered' for d in decisions)
        else:
            passed=len(decisions)==1 and decisions[0]['recommendation']==expected['recommendation']
            if passed and expected['recommendation']!='candidate':
                passed=decisions[0]['pair']['assessment']['deadline']['value']==expected['value']
            if passed and 'content_type' in expected:
                passed=decisions[0].get('content_type')==expected['content_type']
        report['cases'].append(dict(name=case['name'],request=request,result=result,expected=expected,
            passed=passed,elapsed_seconds=round(time.monotonic()-started,2)))
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(name=case['name'],passed=passed,status=row['status'],
            routes=[d['recommendation'] for d in decisions],seconds=report['cases'][-1]['elapsed_seconds'])),flush=True)
    return 0 if report['cases'] and all(c['passed'] for c in report['cases']) else 1


if __name__=='__main__':raise SystemExit(main())
