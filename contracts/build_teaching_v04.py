"""Build independently usable v0.4 request/result schemas from the approved policy."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from contracts.recognition_errors import schema as failure_schema, diagnostic_schema

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
    # Additive result field: old saved results default to task, never reclassified.
    decision['properties']['content_type'] = dict(enum=['task','notice'])
    decision['properties']['submission_relation'] = dict(enum=['assignment_submission','other','unknown'])
    error = obj(dict(code=dict(enum=['agent_timeout','agent_unavailable','invalid_agent_output','limit_exceeded']),
                     message=string(256,1), retryable=dict(type='boolean')))
    error['properties'].update(diagnostic={'$ref':'#/$defs/recognition_diagnostic'},
        failure={'$ref':'#/$defs/recognition_failure'},
        diagnostic_id=dict(type='string',pattern='^[0-9a-f]{24}$'),
        diagnostics=dict(type='string',maxLength=65536))
    row = obj(dict(input_id=ident, snapshot_id=ident, source=source, attempted=dict(type='boolean'),
        status=dict(enum=['success','no_tasks','incomplete','failed']),
        decisions=array(decision,50), warnings=array(string(512,1),20), error=nullable(error)))
    row['properties']['empty_decision'] = nullable(obj(dict(status=dict(enum=['no_tasks','incomplete']),
        reason=string(512,1), evidence=array({'$ref':'#/$defs/quote'},20))))
    # Full manifests are server run metadata. JSON carries only their digest.
    policy = nullable(dict(type='string',pattern='^teaching-policy-v1:[0-9a-f]{64}$'))
    result = obj(dict(contract_version=dict(const='0.4'),request_id=ident,policy_version=policy,
        status=dict(enum=['success','partial_success','no_tasks','failed']),record_results=array(row,20)))
    result['properties']['classification_mode'] = dict(const='model-v1')
    request = obj(dict(contract_version=dict(const='0.4'),request_id=ident,records=array(entry,20)))
    outputs = {name:dict(value, **{'$schema':POLICY['$schema'], '$defs':deepcopy(defs)})
               for name,value in [('request',request),('result',result)]}
    outputs['result']['$defs'].update(recognition_failure=failure_schema(),recognition_diagnostic=diagnostic_schema())
    return outputs


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    outputs = {f'teaching-{name}-v0.4.schema.json': value for name,value in schemas().items()}
    request = deepcopy(schemas()['request']); defs = request.pop('$defs'); request.pop('$schema')
    request['properties']['records'].update(minItems=1, maxItems=1)
    ident = deepcopy(POLICY['properties']['record']['properties']['input_id'])
    assignment = obj(dict(course_id=ident, content_id=ident,
        state=dict(enum=['submitted','active','unknown']),
        basis=dict(enum=['review_submission_history_title','active_assignment_page','unknown'])))
    outputs['teaching-capture-v0.1.schema.json'] = dict(obj(dict(
        contract_version=dict(const='teaching-capture/0.1'), request=request, assignment=nullable(assignment))),
        **{'$schema': POLICY['$schema'], '$defs': defs})
    for name,value in outputs.items():
        path=ROOT/'schemas'/name
        text=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
        if args.check:
            if path.read_text(encoding='utf-8')!=text:raise SystemExit('Schema needs regeneration: '+str(path))
        else:path.write_text(text,encoding='utf-8')


if __name__=='__main__':main()
