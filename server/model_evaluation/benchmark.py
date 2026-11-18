"""Run stratified Qiniu experiments in isolated workers with a shared CNY ledger."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
from unittest.mock import patch
from . import runner
from .budget import Budget
from .benchmark_client import BenchmarkClient


def admit(profiles,estimates,remaining,existing=None):
    if existing is not None:
        by_family={profile['family']:profile for profile in profiles}
        return [by_family[family] for family in existing]
    selected=[]
    for profile in sorted(profiles,key=lambda p:estimates.get(p['family'],float('inf'))):
        estimate=estimates.get(profile['family'])
        if type(estimate) in (int,float) and 0<estimate<=remaining:
            selected.append(profile);remaining-=estimate
    return selected


def execute(job):
    case,profile,out,base,run_id,ledger,limit=job
    budget=Budget(ledger,limit)
    try:
        with patch.object(runner,'RecordedClient',lambda key,trace:BenchmarkClient(key,trace,profile,budget)):
            result=runner.run(case,profile['id'],Path(out),Path(base),run_id,profile['endpoint'])
        return dict(case=case['id'],model=profile['id'],status=result['status'],error=result.get('error'))
    except (ValueError,OSError,KeyError)as error:
        return dict(case=case['id'],model=profile['id'],status='interrupted',error=type(error).__name__)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,required=True)
    parser.add_argument('--profiles',type=Path,default=runner.FIXTURES/'models.json')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--limit-yuan',type=float,required=True)
    parser.add_argument('--source-version',required=True)
    parser.add_argument('--workers',type=int,default=4,choices=range(1,5))
    parser.add_argument('--family',action='append')
    parser.add_argument('--module',action='append',choices=runner.MODULES)
    parser.add_argument('--origin',action='append')
    parser.add_argument('--probe',action='store_true')
    parser.add_argument('--admission',type=Path,help='Per-family full-suite estimate from probe; required for formal runs')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    cases=runner.load_cases(args.suite/'cases.json',args.suite/'vision-cases.json')
    if args.origin:cases=[c for c in cases if c.get('origin')in args.origin]
    if args.module:cases=[c for c in cases if c['module']in args.module]
    profiles=json.loads(args.profiles.read_text())
    if args.family:profiles=[p for p in profiles if p['family']in args.family]
    if not profiles:parser.error('no selected model profiles')
    if not args.probe and not args.dry_run and not args.admission:parser.error('formal runs require probe admission estimates')
    if args.probe:
        cases=[next(c for c in cases if c['module']==m)for m in runner.MODULES if any(c['module']==m for c in cases)]
    manifest=dict(version='benchmark/1',source_version=args.source_version,profiles=profiles,cases=cases,
                  dataset_hash=runner.digest(cases),profile_hash=runner.digest(profiles),
                  tool_hashes={p.name:runner.digest(p.read_text())for p in Path(__file__).parent.glob('*.py')if not p.name.startswith('.')},
                  timeouts=dict(recognition=45,vision=45,decomposition=20,estimation=20),workers=args.workers,
                  repeats='explicit case IDs; no cached replay counts as a repeated call',max_output_tokens=4096)
    if args.admission:
        estimates=json.loads(args.admission.read_text())
        existing_path=args.out/'benchmark-manifest.json'
        existing=json.loads(existing_path.read_text()).get('admitted_families') if existing_path.exists() else None
        remaining=args.limit_yuan-Budget.read_summary(args.ledger,args.limit_yuan)['committedYuan']
        profiles=admit(profiles,estimates,remaining,existing)
        if not profiles:parser.error('no complete model fits the remaining budget')
    manifest['admitted_families']=[p['family']for p in profiles]
    manifest['admission_estimates']=estimates if args.admission else None
    manifest['run_id']=runner.digest(manifest)
    jobs=[(case,p,str(args.out),str(args.suite),manifest['run_id'],str(args.ledger),args.limit_yuan)
          for p in profiles for case in cases if case['module']!='vision' or 'image'in p['modalities']]
    if args.dry_run:
        print(json.dumps(dict(cases=len(cases),families=len({c.get('family_id',c['id'])for c in cases}),jobs=len(jobs),run_id=manifest['run_id'])))
        return
    os.umask(0o077);args.out.mkdir(parents=True,exist_ok=True)
    path=args.out/'benchmark-manifest.json'
    if path.exists() and json.loads(path.read_text())!=manifest:parser.error('manifest differs; use a new output directory')
    runner.write_json(path,manifest)
    budget=Budget(args.ledger,args.limit_yuan)
    if not os.environ.get('QINIU_API_KEY'):parser.error('QINIU_API_KEY is required for new calls')
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers)as pool:
        pending={}
        iterator=iter(jobs)
        while True:
            while len(pending)<args.workers and budget.summary()['committedYuan']<args.limit_yuan and not budget.summary()['boundExceeded']:
                try:job=next(iterator)
                except StopIteration:break
                pending[pool.submit(execute,job)]=job
            if not pending:break
            done,_=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
            exhausted=False
            for future in done:
                pending.pop(future)
                result=future.result();print(json.dumps(result),flush=True)
                if result.get('error')=='BudgetExceeded':exhausted=True
            if exhausted:
                for future in pending:print(json.dumps(future.result()),flush=True)
                break
    runner.write_json(args.out/'budget-summary.json',budget.summary())
    print(json.dumps(budget.summary()),flush=True)

if __name__=='__main__':main()
