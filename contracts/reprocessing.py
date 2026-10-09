"""#112 standalone operation schemas and pure checks; no queue, DB or authentication."""
from datetime import datetime
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

VERSION = 'reprocessing/0.1'
ROOT = Path(__file__).resolve().parent
ID = dict(type='string', pattern=r'^[A-Za-z0-9_.:-]{1,160}$')
LABEL = dict(type='string', minLength=1, maxLength=512, pattern=r'^[^\u0000-\u001f\u007f]+$')
POLICY = dict(type='string', pattern=r'^text-policy-v1:[0-9a-f]{64}$')
ROW_STATES = ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted', 'superseded')
FINAL = {'succeeded', 'partial_success', 'failed', 'stopped', 'interrupted'}
PROTECTED = {'confirmed', 'ignored', 'edited', 'completed', 'deleted'}


def obj(fields):
    return dict(type='object', properties=fields, required=list(fields), additionalProperties=False)


def arr(items, maximum=100, minimum=0, unique=False):
    return dict(type='array', items=items, minItems=minimum, maxItems=maximum, uniqueItems=unique)


def nullable(value):
    return dict(anyOf=[value, dict(type='null')])


def envelope(fields):
    return obj(dict(contract_version=dict(const=VERSION), **fields))


def bundle():
    count = dict(type='integer', minimum=0, maximum=100)
    stamp = dict(type='string', format='date-time', maxLength=40)
    error = obj(dict(code=dict(enum=['agent_timeout', 'agent_unavailable', 'invalid_result', 'interrupted']),
                     message=LABEL))
    summary = obj({k:dict(type='integer', minimum=0, maximum=1000)
                   for k in ('preserved', 'replaced', 'new_pending', 'unresolved')})
    row = obj(dict(input_id=ID, state=dict(enum=list(ROW_STATES)), attempted=dict(type='boolean'),
        policy_version=nullable(POLICY), previous_available=dict(type='boolean'),
        old_result_retained=dict(type='boolean'), outcome=nullable(dict(enum=['tasks', 'no_tasks'])),
        decision_summary=summary, error=nullable(error)))
    run = envelope(dict(run_id=ID, operation_id=ID, source_account_id=ID,
        revision=dict(type='integer', minimum=1), state=dict(enum=['queued', 'running', 'stopping', *sorted(FINAL)]),
        created_at=stamp, updated_at=stamp, records=arr(row, minimum=1),
        counts=obj(dict(total=count, **{k:count for k in ROW_STATES}))))
    entry = obj(dict(input_id=ID, label=LABEL, course_or_project=nullable(LABEL),
        result_status=dict(enum=['success', 'no_tasks', 'failed', 'unknown']), policy_version=nullable(POLICY)))
    account = obj(dict(source_account_id=ID, label=LABEL, records=arr(entry)))
    cursor = nullable(dict(type='string', minLength=1, maxLength=512))
    roots = dict(
        start=envelope(dict(operation_id=ID, source_account_id=ID, input_ids=arr(ID, minimum=1, unique=True))),
        stop=envelope({}), run=run,
        sources=envelope(dict(accounts=arr(account, maximum=50), next_cursor=cursor)),
        runs=envelope(dict(runs=arr(run, maximum=20), next_cursor=cursor)),
        error=envelope(dict(code=dict(enum=['invalid_request', 'not_found', 'conflict', 'unavailable']),message=LABEL)),
        association=envelope(dict(
            previous=arr(obj(dict(action_id=ID, decision=dict(enum=['pending', *sorted(PROTECTED)])))),
            candidates=arr(ID, unique=True),
            links=arr(obj(dict(candidate_id=ID, action_id=ID))),
            confirmed_new=arr(ID, unique=True))))
    return {'$schema':'https://json-schema.org/draft/2020-12/schema', '$defs':roots}


def root_schema(kind):
    data=bundle()
    if kind not in data['$defs']:raise ValueError('unknown reprocessing envelope')
    return dict(data, **{'$ref':'#/$defs/'+kind})


def require(condition, reason):
    if not condition:raise ValueError(reason)


def unique(items, key):
    require(len({i[key] for i in items})==len(items),'duplicate '+key)


def check(kind, value):
    require(len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))<=2_000_000,'envelope too large')
    Draft202012Validator(root_schema(kind),format_checker=FormatChecker()).validate(value)
    if kind=='run':_check_run(value)
    elif kind=='runs':
        unique(value['runs'],'run_id')
        for run in value['runs']:_check_run(run)
    elif kind=='sources':
        unique(value['accounts'],'source_account_id')
        for account in value['accounts']:unique(account['records'],'input_id')
    elif kind=='association':_check_association(value)
    return value


def _time(text):
    return datetime.fromisoformat(text.replace('Z','+00:00').replace('z','+00:00'))


def _check_run(run):
    rows=run['records'];unique(rows,'input_id')
    require(_time(run['updated_at'])>=_time(run['created_at']),'time moved backwards')
    counts={state:sum(r['state']==state for r in rows) for state in ROW_STATES}
    require(run['counts']==dict(total=len(rows),**counts),'counts do not describe records')
    for row in rows:
        state=row['state'];attempted=row['attempted'];previous=row['previous_available']
        require(not row['old_result_retained'] or previous,'cannot retain a missing old result')
        require(attempted or row['policy_version'] is None,'unattempted record has a policy version')
        require(state not in ('queued','cancelled') or not attempted,'unstarted state was attempted')
        require(state not in ('running','succeeded','failed') or attempted,'execution state was not attempted')
        require((row['error'] is not None)==(state in ('failed','interrupted')),'error/state mismatch')
        if state=='succeeded':
            require(row['policy_version'] is not None and row['outcome'] is not None,'success lacks actual version/outcome')
            if row['outcome']=='no_tasks':
                require(row['decision_summary']['new_pending']==0 and row['decision_summary']['unresolved']==0,'no_tasks contains new candidates')
            if row['decision_summary']['unresolved']:
                require(row['old_result_retained']==previous,'ambiguous mapping discards old results')
        else:
            require(row['outcome'] is None and not any(row['decision_summary'].values()),'non-success applies decisions')
            require(row['old_result_retained']==previous,'non-success lost previous result')
    state=run['state'];active=counts['queued']+counts['running']
    if state=='queued':require(counts['queued']==len(rows),'queued run already progressed')
    elif state=='running':require(active>0 and not counts['cancelled'] and not counts['interrupted'],'running run has no active work or is stopped')
    elif state=='stopping':require(counts['running']>0 and counts['queued']==0,'stopping must cancel queued records')
    else:
        require(active==0,'terminal run contains active work')
        if state=='succeeded':require(counts['succeeded']==len(rows),'successful run has unfinished results')
        elif state=='failed':require(counts['failed']==len(rows),'failed run includes unprocessed records')
        elif state=='partial_success':
            require(counts['succeeded']>0 and counts['succeeded']<len(rows) and not counts['cancelled'] and not counts['interrupted'],
                    'partial_success needs successful and failed/superseded rows')
        elif state=='stopped':require(counts['cancelled']+counts['interrupted']>0,'stopped run has no stopped work')
        elif state=='interrupted':require(counts['interrupted']+counts['superseded']>0,'interruption needs interrupted/superseded work')


def check_start_run(start, run):
    check('start',start);check('run',run)
    require(all(start[k]==run[k] for k in ('operation_id','source_account_id')),'run belongs to another operation')
    require(set(start['input_ids'])=={r['input_id'] for r in run['records']},'run selection differs')


def same_operation(first, retry):
    """Call only within the same authenticated account; order is not identity."""
    check('start',first);check('start',retry)
    return (first['operation_id']==retry['operation_id'] and first['source_account_id']==retry['source_account_id']
            and set(first['input_ids'])==set(retry['input_ids']))


def check_transition(before, after):
    check('run',before);check('run',after)
    if before==after:return after
    require(before['state'] not in FINAL,'terminal run is immutable')
    for key in ('run_id','operation_id','source_account_id','created_at'):
        require(before[key]==after[key],'run identity changed')
    require(after['revision']==before['revision']+1,'revision must increment once per committed snapshot')
    require(_time(after['updated_at'])>=_time(before['updated_at']),'update time moved backwards')
    allowed={'queued':{'running','stopped','interrupted'},
             'running':{'running','stopping',*FINAL},'stopping':{'stopping','stopped','interrupted'}}
    require(after['state'] in allowed[before['state']],'invalid run transition')
    require([r['input_id'] for r in before['records']]==[r['input_id'] for r in after['records']],'record identities/order changed')
    transitions={'queued':{'running','cancelled','interrupted','superseded'},
                 'running':{'running','succeeded','failed','interrupted','superseded'}}
    for old,new in zip(before['records'],after['records']):
        if old==new:continue
        require(old['previous_available']==new['previous_available'],'previous-result availability changed')
        require(new['state'] in transitions.get(old['state'],set()),'terminal record rewritten or invalid transition')
        require(not old['attempted'] or new['attempted'],'attempt was erased')
        require(old['policy_version'] is None or old['policy_version']==new['policy_version'],'actual policy overwritten')
        if before['state']=='stopping':require(new['state'] in ('interrupted','superseded'),'stopped job applies late result')
    return after


def may_commit(run, input_id, *, latest_run_id):
    """Reference guard, called inside the server's account-scoped transaction."""
    check('run',run)
    return run['state']=='running' and run['run_id']==latest_run_id and any(
        r['input_id']==input_id and r['state']=='running' and r['policy_version'] is not None for r in run['records'])


def _check_association(value):
    unique(value['previous'],'action_id');unique(value['links'],'candidate_id');unique(value['links'],'action_id')
    old={x['action_id'] for x in value['previous']};new=set(value['candidates'])
    require(all(x['action_id'] in old and x['candidate_id'] in new for x in value['links']),'unknown action link')
    require(set(value['confirmed_new']).issubset(new),'unknown confirmed-new candidate')
    require(not set(value['confirmed_new']).intersection(x['candidate_id'] for x in value['links']),'new candidate already linked')


def association_plan(value):
    """Pure reference plan from SERVER-verified or explicit human associations.

    This object is not accepted by start/stop APIs. It neither matches titles nor
    writes tasks. Server must bind it to account, source, input and run identities.
    """
    check('association',value)
    old={x['action_id']:x['decision'] for x in value['previous']}
    links={x['candidate_id']:x['action_id'] for x in value['links']}
    protected={a for a,decision in old.items() if decision in PROTECTED}
    unresolved=[];new_pending=[];suppressed=[]
    for candidate in value['candidates']:
        target=links.get(candidate)
        if target in protected:suppressed.append(candidate)
        elif target is not None or not protected or candidate in value['confirmed_new']:new_pending.append(candidate)
        else:unresolved.append(candidate)
    pending={a for a,decision in old.items() if decision=='pending'}
    return dict(preserved_action_ids=sorted(protected),
        replace_pending_ids=[] if unresolved else sorted(pending),
        retained_pending_ids=sorted(pending) if unresolved else [],
        new_pending_ids=[] if unresolved else new_pending,
        suppressed_candidate_ids=suppressed, unresolved_candidate_ids=unresolved,
        hold_record=bool(unresolved))


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    path=ROOT/'schemas/reprocessing-v0.1.schema.json'
    text=json.dumps(bundle(),ensure_ascii=False,indent=2)+'\n'
    if args.check:require(path.read_text(encoding='utf-8')==text,'regenerate reprocessing schema')
    else:path.write_text(text,encoding='utf-8')


if __name__=='__main__':main()
