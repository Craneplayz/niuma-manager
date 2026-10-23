"""Opt-in real-model course reasoning acceptance using synthetic sources only.

The selected material texts are assembled by the production retrieval code.
Expected dates/routes are independent assertions, not canned model responses.
Run a case more than once to measure variability; a pass is not a guarantee.
"""
import argparse
import json
import time
from pathlib import Path
from copy import deepcopy

from ddl_text.chat_client import ChatClient
from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.teaching_pipeline import process_teaching_request
from contracts.teaching_io import snapshot_digest


def cases():
    path=Path(__file__).resolve().parents[2]/'contracts/examples/course-context-v01.json'
    return json.loads(path.read_text(encoding='utf-8'))['cases']


def evaluate(result,expected):
    decisions=[d for row in result['record_results'] for d in row['decisions'] if d['recommendation']!='filtered']
    errors=[]
    if result['status'] not in ('success','no_tasks'):errors.append('recognition failed')
    if len(decisions)!=expected['count']:errors.append('item count')
    if sorted(d['recommendation'] for d in decisions)!=sorted(expected['routes']):errors.append('routing')
    dates=[d['pair']['assessment']['deadline']['value'] for d in decisions]
    if 'dates' in expected and sorted(dates,key=str)!=sorted(expected['dates'],key=str):errors.append('dates')
    if 'channels' in expected and not all(any(c in d['title'] for d in decisions) for c in expected['channels']):errors.append('delivery channels')
    if 'title' in expected and (len(decisions)!=1 or decisions[0]['title']!=expected['title']):errors.append('title consistency')
    if expected.get('calendar') and not any(d.get('calendar_rule') for d in decisions):errors.append('calendar proof')
    if expected.get('context_date') and not all(any(q['part_id'].startswith('context:') for q in d['pair']['assessment']['deadline']['evidence']) for d in decisions):errors.append('cross-file evidence')
    return errors


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true');parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--cases',nargs='+',choices=[c['name'] for c in cases()])
    args=parser.parse_args()
    if not args.live or args.output.exists():parser.error('require --live and a new report path')
    options=live_options(args.env_file)
    if options['provider']!='qiniu':parser.error('requires configured Qiniu provider')
    settings=Settings(mode='real',model=options['model'],endpoint=options['endpoint'],timeout_seconds=90,max_retries=0,
        thinking_mode='disabled' if options['model'].startswith('deepseek/') else None)
    client=ChatClient(options['key']);report=dict(synthetic_only=True,model=settings.model,cases=[])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    # Shared, reviewed inputs and expectations are frozen independently of a
    # model run; source producers and consumers validate these same fixtures.
    for case in cases():
        if args.cases and case['name'] not in args.cases:continue
        request=deepcopy(case['request']);started=time.monotonic()
        result=process_teaching_request(request,client=client,settings=settings,budget_seconds=95,
            verified_snapshot_digests={snapshot_digest(e) for e in request['records']})
        failures=evaluate(result,case['expected'])
        report['cases'].append(dict(case,result=result,failures=failures,seconds=round(time.monotonic()-started,2)))
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(name=case['name'],passed=not failures,failures=failures,seconds=report['cases'][-1]['seconds'])),flush=True)
    return 0 if report['cases'] and all(not c['failures'] for c in report['cases']) else 1


if __name__=='__main__':raise SystemExit(main())
