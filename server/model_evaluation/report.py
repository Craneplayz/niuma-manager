"""Publish scored benchmark rows without private teaching originals or raw call logs."""
import argparse
import json
import math
from datetime import datetime,timezone
from pathlib import Path
from .runner import digest,write_json
from .metrics import aggregate
from .course_context_suite import score as score_course_context


def evaluate(case,receipt):
    ok=receipt.get('status')=='ok';out=receipt.get('output');expected=case['expected'];checks={'contract':ok}
    tp=fp=fn=0
    if case.get('input_contract')=='0.4':
        record=((out or {}).get('record_results')or[{}])[0]
        decisions=record.get('decisions',[])
        deadlines=[d.get('pair',{}).get('assessment',{}).get('deadline',{}) for d in decisions]
        decision_text=' '.join(d.get('title','')+' '+d.get('description','') for d in decisions)
        if expected.get('deadline'):
            checks['deadline']=ok and any(d.get('value')==expected['deadline'] for d in deadlines)
            checks['deadline_role']=ok and any(decision.get('time_role')=='deadline' and deadline.get('value')==expected['deadline'] for decision,deadline in zip(decisions,deadlines))
        if expected.get('event'):checks['event_time']=ok and any(d.get('time_role')=='event' and d.get('pair',{}).get('assessment',{}).get('deadline',{}).get('value')==expected['event'] for d in decisions)
        if expected.get('reading'):checks['reading_retained']=ok and '阅读'in decision_text
        if expected.get('no_action'):checks['negative_correct']=ok and not decisions
        if expected.get('no_fabricated_deadline'):checks['no_fabricated_deadline']=ok and all(d.get('value') is None for d in deadlines)
        if expected.get('incomplete'):checks['semantic_truth']=None
    elif case['module']in ('recognition','vision'):
        tasks=(out if isinstance(out,list)else(out or {}).get('tasks',[]))if ok else[]
        terms=expected.get('terms',[]);used=set();matches={}
        for term in terms:
            found=next((i for i,t in enumerate(tasks)if i not in used and term in(t.get('title','')+' '+t.get('description',''))),None)
            if found is not None:used.add(found);matches[term]=tasks[found]
        tp=len(matches);fp=len(tasks)-tp;fn=len(terms)-tp
        checks['count']=ok and len(tasks)==expected['count']
        if not expected.get('incomplete'):checks['no_unmatched_task']=ok and fp==0
        for i,target in enumerate(expected.get('targets',[])):
            task=matches.get(target.get('term'))
            deadline=(task or {}).get('deadline',{})
            checks['date_'+str(i)]=ok and task is not None and deadline.get('value')==target['value'] and deadline.get('precision')==target['precision']
        if expected.get('incomplete'):
            tp=fp=fn=0
            checks.pop('count',None)
            checks['semantic_truth']=None
            checks['no_fabricated_deadline']=ok and all(t.get('deadline',{}).get('value') is None for t in tasks)
        elif expected['count']==0:checks['negative_correct']=ok and not tasks
    elif case['module']=='decomposition':
        output=out or{};plan=output.get('plan')or{};steps=plan.get('subtasks',[])
        checks['usable_plan']=ok and bool(steps)
        checks['state']=ok and output.get('status')in expected.get('acceptable_statuses',[expected.get('status')])
        words=json.dumps(steps,ensure_ascii=False)
        for i,action in enumerate(expected.get('required_actions',[])):checks['action_'+str(i)]=ok and action in words
        checks['no_invented_schedule']=ok and all(not s.get('scheduled_start')and not s.get('scheduled_end')for s in steps)
    else:
        estimates=(out or{}).get('estimates',{});ids={t['id']for t in case['request']['tasks']}
        checks['positive_complete']=ok and set(estimates)==ids and all(type(v)in(int,float)and math.isfinite(v)and v>0 for v in estimates.values())
        targets=expected.get('estimates',{})
        if 'minutes'in expected and expected['minutes']is not None:targets={next(iter(ids)):expected['minutes']}
        for tid,value in targets.items():checks['explicit_'+tid]=checks['positive_complete']and estimates.get(tid)==value
    return checks,tp,fp,fn


def read_run(folder):
    manifest_path=folder/'benchmark-manifest.json'
    manifest=json.loads(manifest_path.read_text());receipts={}
    for path in folder.glob('*.json'):
        if path.name in ('benchmark-manifest.json','budget-summary.json'):continue
        r=json.loads(path.read_text())
        if r.get('run_id')==manifest['run_id'] and r.get('status') in ('ok','error'):receipts[(r['id'],r['model'])]=r
    return manifest,receipts,manifest_path.stat().st_mtime


def publish(folder,target,phase="unspecified",additional=()):
    manifest,receipts,started=read_run(folder)
    run_ids=[manifest['run_id']]
    profiles={profile['id']:profile for profile in manifest['profiles']}
    admitted_families=set(manifest.get('admitted_families',[profile['family'] for profile in profiles.values()]))
    budget_folder=folder
    for extra_folder in additional:
        extra,extra_receipts,_=read_run(extra_folder)
        for key in ('cases','dataset_hash','source_version','tool_hashes','timeouts','max_output_tokens','workers'):
            if manifest.get(key)!=extra.get(key):raise ValueError(f'incompatible comparison runs: {key}')
        if receipts.keys() & extra_receipts.keys():raise ValueError('duplicate case/model receipts in comparison runs')
        for profile in extra['profiles']:
            if profiles.setdefault(profile['id'],profile)!=profile:raise ValueError('incompatible model settings')
        admitted_families.update(extra['admitted_families'])
        receipts.update(extra_receipts);run_ids.append(extra['run_id']);budget_folder=extra_folder
    rows=[]
    for case in manifest['cases']:
        for profile in profiles.values():
            applicable=case['module']!='vision'or'image'in profile['modalities']
            receipt=receipts.get((case['id'],profile['id']))
            executed=receipt is not None and receipt.get('error')!='BudgetExceeded'
            r=dict(receipt) if executed else{}
            original_status=r.get('status')
            if isinstance(r.get('output'),dict) and r['output'].get('status')=='partial_success' and not r.get('error'):
                r['status']='ok'
            admitted=profile['family'] in admitted_families
            checks,tp,fp,fn=evaluate(case,r)if executed else({},0,0,0)
            calls=r.get('observations',[])
            tokens={key:sum(c[key]for c in calls)if calls and all(type(c.get(key))==int for c in calls)else None for key in ('input_tokens','output_tokens','total_tokens')}
            costs=[c.get('estimated_yuan')for c in calls]
            cost=sum(costs)if costs and all(type(c)in(int,float)for c in costs)else None
            shareable=case.get('shareable',False)
            rows.append(dict(id=case['id'],family_id=case.get('family_id',case['id']),model=profile['id'],family=profile['family'],module=case['module'],
                origin=case.get('origin','synthetic'),scenario=case.get('scenario',case.get('slice','unknown')),difficulty=case.get('difficulty','standard'),
                purpose=case.get('purpose',case.get('scenario',case.get('slice',''))),repeat_index=case.get('repeat_index',0),transformation=case.get('transformation'),pair_with=case.get('pair_with'),label_status=case.get('label_status','frozen_synthetic'),applicable=applicable,admitted=admitted,executed=executed,status=r.get('status','pending'if admitted else'not_selected_budget'),
                executionStatus=original_status,businessStatus=(r.get('output')or{}).get('status')if isinstance(r.get('output'),dict)else None,checks=checks,tp=tp,fp=fp,fn=fn,seconds=r.get('seconds'),estimated_yuan=cost,**tokens,
                course_context=score_course_context(case['expected'],r.get('output') or {}) if executed and case['expected'].get('course_context') else None,
                calls=len(calls),call_usage=[{k:c.get(k)for k in ('input_tokens','output_tokens','total_tokens','duration_ms','estimated_yuan','result_code','http_status','provider_request_id','usage_details')}for c in calls],error=r.get('code')or r.get('error'),
                input=case.get('record',case.get('request'))if shareable else '真实课程材料：原文仅保留在私有评测目录',
                expected=case['expected']if shareable else {'checks':list(checks)},output=r.get('output')if shareable else None))
    by_identity={(row['model'],row['id']):row for row in rows}
    for row in rows:
        if not row['executed'] or not row['pair_with']:continue
        original=by_identity.get((row['model'],row['pair_with']))
        if not original or not original['executed'] or row['module']!=original['module']:continue
        metric='repeat_fidelity' if row['repeat_index'] else 'paired_fidelity'
        row['checks'][metric]=all(value for value in (*row['checks'].values(),*original['checks'].values()) if value is not None)
    result=dict(version='benchmark-report/1',id=run_ids[0] if len(run_ids)==1 else digest(run_ids),runIds=run_ids,phase=phase,runStartedAt=datetime.fromtimestamp(started,timezone.utc).isoformat(),sourceVersion=manifest['source_version'],
        scoringHash=digest(Path(__file__).read_text()),toolHashes=manifest.get('tool_hashes',{}),timeouts=manifest.get('timeouts',{}),
        datasetHash=manifest['dataset_hash'],profiles=list(profiles.values()),rows=rows,
        summary=aggregate(rows),budget=json.loads((budget_folder/'budget-summary.json').read_text())if(budget_folder/'budget-summary.json').exists()else None,
        metricDefinitions=dict(precision='TP/(TP+FP)',recall='TP/(TP+FN)',f1='2TP/(2TP+FP+FN)',latency='含失败请求的整例P50/P90/P95/max',cost='供应商标准价最高档估算，非现金扣款',confidence='同源变换/重复合组后全通过率的95% Wilson区间'))
    write_json(target,result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--results',type=Path,required=True);p.add_argument('--additional-results',type=Path,action='append',default=[]);p.add_argument('--out',type=Path,required=True);p.add_argument('--phase',choices=['probe','formal','diagnostic'],required=True);a=p.parse_args()
    r=publish(a.results,a.out,phase=a.phase,additional=a.additional_results);print(json.dumps(dict(id=r['id'],rows=len(r['rows']))))
