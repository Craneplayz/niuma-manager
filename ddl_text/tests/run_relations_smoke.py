"""Explicit synthetic-only live evaluation; saves sanitized model outputs on request."""
import argparse
import json
from pathlib import Path
from ddl_text.task_relations import relate
from ddl_text.chat_client import ChatClient
from ddl_text.codex_client import live_options
from ddl_text.config import Settings


def assess(case,result):
    kinds={n['id']:n['kind'] for n in case['nodes']}
    actual=sorted(sorted(i for i in g['members'] if kinds[i]!='source') for g in result['groups'])
    expected=case['expected'];errors=[]
    if actual!=sorted(sorted(g) for g in expected['groups']):errors.append('action_groups')
    if expected.get('review') and not all(g['disposition']=='review' for g in result['groups']):errors.append('review')
    if expected.get('due') and not all(g['result']['due'] and g['result']['due']['date']==expected['due'] for g in result['groups']):errors.append('due')
    if 'due_time' in expected and not all(g['result']['due'] and g['result']['due']['time']==expected['due_time'] for g in result['groups']):errors.append('due_time')
    if expected.get('apply') and not all(g['disposition']=='apply' for g in result['groups']):errors.append('apply')
    for source in expected.get('complete_sources',[]):
        for group in result['groups']:
            actions={i for i in group['members'] if kinds[i]!='source'}
            if not any(c.get('source_id')==source and set(c['covers'])==actions for c in group['completion']):errors.append('completion_scope')
    for sources in expected.get('joint_complete_sources',[]):
        if not any(set(c.get('source_ids',[]))==set(sources) and set(c['covers'])=={i for i in g['members'] if kinds[i]!='source'}
                   for g in result['groups'] for c in g['completion']):errors.append('joint_completion_scope')
    return sorted(set(errors))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--env',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();options=live_options(args.env)
    settings=Settings(mode='real',model=options['model'],endpoint=options['endpoint'],timeout_seconds=90,max_retries=0,
        thinking_mode='disabled' if options['model'].startswith('deepseek/') else None)
    cases=json.loads((Path(__file__).parents[2]/'contracts/examples/task-relations-v01.json').read_text(encoding='utf-8'))['cases']
    results=[]
    for case in cases:
        try:
            result=relate(case['nodes'],client=ChatClient(options['key']),settings=settings)
            errors=assess(case,result)
        except Exception as error:result=None;errors=[type(error).__name__]
        results.append(dict(name=case['name'],input=case['nodes'],expected=case['expected'],output=result,errors=errors))
        print(case['name']+': '+('PASS' if not errors else ','.join(errors)),flush=True)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(dict(model=settings.model,synthetic=True,cases=results),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if any(r['errors'] for r in results):raise SystemExit(1)


if __name__=='__main__':main()
