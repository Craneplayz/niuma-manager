"""Build independently usable v0.4 request/result schemas from the approved policy."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POLICY = json.loads((ROOT/'schemas/teaching-review-pair-v0.4-draft.schema.json').read_text(encoding='utf-8'))


def obj(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def array(items, maximum):
    return dict(type='array', items=items, maxItems=maximum)


def string(maximum, minimum=0):
    return dict(type='string', minLength=minimum, maxLength=maximum)


def nullable(value):
    return dict(anyOf=[value, dict(type='null')])


def schemas():
    defs = deepcopy(POLICY['$defs'])
    ident = deepcopy(POLICY['properties']['record']['properties']['input_id'])
    pair = {k:deepcopy(v) for k,v in POLICY.items() if k not in ('$schema', '$defs', 'title')}
    source = obj(dict(label=nullable(string(512, 1)), url=nullable(string(2048, 1)),
                      course_or_project=nullable(string(512, 1))))
    entry = obj(dict(record=deepcopy(POLICY['properties']['record']), source=source))
    relevance = obj(dict(state=dict(enum=['current','ongoing','historical','uncertain']),
        reason=string(512,1), evidence=array({'$ref':'#/$defs/quote'},20)))
    decision = obj(dict(title=string(512,1), description=string(4096),
        notice_type=dict(enum=['create','update','postpone','cancel','uncertain']),
        target=nullable({'$ref':'#/$defs/quote'}), relevance=relevance, pair=pair,
        recommendation=dict(enum=['task','candidate','filtered']),
        review_codes=array(string(512,1),100), review_reasons=array(string(1024,1),100),
        filter_reason=nullable(string(1024,1))))
    error = obj(dict(code=dict(enum=['agent_timeout','agent_unavailable','invalid_agent_output','limit_exceeded']),
                     message=string(256,1), retryable=dict(type='boolean')))
    row = obj(dict(input_id=ident, snapshot_id=ident, source=source, attempted=dict(type='boolean'),
        status=dict(enum=['success','no_tasks','incomplete','failed']),
        decisions=array(decision,50), warnings=array(string(512,1),20), error=nullable(error)))
    # Full manifests are server run metadata. JSON carries only their digest.
    policy = nullable(dict(type='string',pattern='^teaching-policy-v1:[0-9a-f]{64}$'))
    result = obj(dict(contract_version=dict(const='0.4'),request_id=ident,policy_version=policy,
        status=dict(enum=['success','partial_success','no_tasks','failed']),record_results=array(row,20)))
    request = obj(dict(contract_version=dict(const='0.4'),request_id=ident,records=array(entry,20)))
    return {name:dict(value, **{'$schema':POLICY['$schema'], '$defs':deepcopy(defs)})
            for name,value in [('request',request),('result',result)]}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    for name,value in schemas().items():
        path=ROOT/f'schemas/teaching-{name}-v0.4.schema.json'
        text=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
        if args.check:
            if path.read_text(encoding='utf-8')!=text:raise SystemExit('Schema needs regeneration: '+str(path))
        else:path.write_text(text,encoding='utf-8')


if __name__=='__main__':main()
