"""#110 pure submission-state decisions; callers own provenance and transactions."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT=Path(__file__).resolve().parent
VERSION='submission-policy/0.1'
ID=dict(type='string',minLength=1,maxLength=160,pattern=r'^[A-Za-z0-9_.:-]+$')


def obj(fields):return dict(type='object',properties=fields,required=list(fields),additionalProperties=False)


def schema():
    snapshot=obj(dict(source_account_id=ID,input_id=ID,course_id=ID,content_id=ID,snapshot_id=ID,
        sequence=dict(type='integer',minimum=1),observed_at=dict(type='string',format='date-time',maxLength=40),
        state=dict(enum=['submitted','active','unknown']),
        basis=dict(enum=['review_submission_history_title','active_assignment_page','unknown'])))
    action=obj(dict(action_id=ID,kind=dict(enum=['candidate','task']),
        relation=dict(enum=['assignment_submission','other','unknown']),
        link_basis=dict(enum=['server_exact','user_confirmed','unresolved']),
        decision=dict(enum=['pending','confirmed','edited','completed','ignored','deleted']),
        completion_origin=dict(enum=[None,'manual','source_submission'])))
    return dict(obj(dict(contract_version=dict(const=VERSION),snapshot=snapshot,
        scope=dict(enum=['submission_only','mixed','unknown']),actions=dict(type='array',items=action,maxItems=100))),
        **{'$schema':'https://json-schema.org/draft/2020-12/schema'})


def require(condition,message):
    if not condition:raise ValueError(message)


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def observation_digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def check(value):
    require(len(canonical(value))<=200_000,'submission observation too large')
    Draft202012Validator(schema(),format_checker=FormatChecker()).validate(value)
    snapshot=value['snapshot']
    expected={'submitted':'review_submission_history_title','active':'active_assignment_page','unknown':'unknown'}
    require(snapshot['basis']==expected[snapshot['state']],'state lacks agreed source evidence')
    require(len({a['action_id'] for a in value['actions']})==len(value['actions']),'duplicate permanent action')
    for action in value['actions']:
        require((action['relation']=='unknown')==(action['link_basis']=='unresolved'),'relation/link mismatch')
        require(value['scope']!='submission_only' or action['relation']=='assignment_submission','submission-only scope has unrelated/unknown action')
        require((action['decision']=='completed')==(action['completion_origin'] is not None),'completion origin mismatch')
        require(action['kind']!='candidate' or action['decision'] in ('pending','ignored','deleted'),'candidate claims saved-task decision')
        require(action['kind']!='task' or action['decision']!='pending','saved task claims pending candidate')
    return value


def decide(value, *, verified_digest=None, source_account_id=None, input_id=None,
           latest_sequence=0, previous_state='unknown'):
    """Supply verified_digest ONLY after account-scoped snapshot/action resolution.

    The digest binds the entire observation, including source and action mapping;
    it is not an authentication mechanism. No DB, model, HTTP or task writes occur.
    """
    check(value)
    require(type(latest_sequence) is int and latest_sequence>=0,'invalid stored sequence')
    require(previous_state in ('submitted','active','unknown'),'invalid stored state')
    snapshot=value['snapshot'];actions=value['actions']
    result=dict(disposition='untrusted',skip_model=False,complete_action_ids=[],withdraw_candidate_ids=[],
        preserve_action_ids=[a['action_id'] for a in actions],needs_review=True,reasons=['source_unverified'])
    if (verified_digest!=observation_digest(value) or source_account_id!=snapshot['source_account_id']
            or input_id!=snapshot['input_id']):return result
    if snapshot['sequence']<=latest_sequence:
        return dict(result,disposition='stale',skip_model=True,needs_review=False,reasons=['stale_snapshot'])
    state=snapshot['state']
    if state!='submitted':
        changed=previous_state=='submitted'
        return dict(result,disposition='reopened_review' if changed else state,skip_model=changed,
            needs_review=changed,reasons=['submission_status_changed'] if changed else [])
    complete=[];withdraw=[]
    for action in actions:
        if action['relation']!='assignment_submission' or action['decision'] in ('ignored','deleted','completed'):continue
        (withdraw if action['kind']=='candidate' else complete).append(action['action_id'])
    affected=set(complete+withdraw)
    unclear=value['scope']=='unknown' or any(a['relation']=='unknown' for a in actions)
    return dict(disposition='needs_scope_review' if unclear else 'submitted',
        skip_model=value['scope']=='submission_only',complete_action_ids=complete,withdraw_candidate_ids=withdraw,
        preserve_action_ids=[a['action_id'] for a in actions if a['action_id'] not in affected],
        needs_review=unclear,reasons=['submission_scope_unknown'] if unclear else [])


def check_successor(before,after):
    """Validate provenance identity/order before updating the account's snapshot."""
    check(before);check(after)
    left=before['snapshot'];right=after['snapshot']
    require(all(left[k]==right[k] for k in ('source_account_id','input_id','course_id','content_id')),'snapshot belongs to another assignment')
    require(right['sequence']>left['sequence'],'snapshot is not newer')
    require(right['snapshot_id']!=left['snapshot_id'],'changed observation reused snapshot identity')
    parse=lambda s:datetime.fromisoformat(s.replace('Z','+00:00').replace('z','+00:00'))
    require(parse(right['observed_at'])>=parse(left['observed_at']),'observation time moved backwards')
    return after


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    path=ROOT/'schemas/submission-policy-v0.1.schema.json';text=json.dumps(schema(),ensure_ascii=False,indent=2)+'\n'
    if args.check:require(path.read_text(encoding='utf-8')==text,'regenerate submission policy schema')
    else:path.write_text(text,encoding='utf-8')


if __name__=='__main__':main()
