"""Text contract 0.2 validation. No model instructions are executed here.

Shared contract snapshot from frontend PR #15, server/text_contract.py,
commit 3251c8eabf1224df9090b0752b94d1c9e3abc0b8. Keep behavior aligned;
the extraction-specific strict field checks live in contracts.py.
Requires the IANA timezone database (install tzdata on Windows).
"""
import copy
import re
from datetime import datetime, date
from zoneinfo import ZoneInfo

PLATFORMS={'teaching_web','wechat','email','web','unknown'}
SUPPORTS={'title','description','prerequisites','course_or_project','notice_type','target_reference','deadline'}

def require(condition):
    if not condition: raise ValueError('文本识别对象不符合 v0.2 契约')

def fields(value,names):
    require(isinstance(value,dict) and all(k in value for k in names.split()))

def string(value,nullable=False,nonempty=False):
    require((nullable and value is None) or (isinstance(value,str) and (not nonempty or bool(value.strip()))))

def strings(value):
    require(isinstance(value,list) and all(isinstance(x,str) for x in value))

def instant(value):
    string(value,nonempty=True)
    require(bool(re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)',value)))
    return datetime.fromisoformat(value.replace('Z','+00:00'))

def timezone(value):
    if value is not None:
        string(value,nonempty=True)
        try: ZoneInfo(value)
        except Exception: raise ValueError('不支持的时区')

def time_value(value,precision):
    require(precision in ['datetime','date','unknown'])
    if precision=='unknown': require(value is None)
    elif precision=='date':
        require(isinstance(value,str) and bool(re.fullmatch(r'\d{4}-\d\d-\d\d',value)))
        date.fromisoformat(value)
    else: instant(value)

def publication(p):
    fields(p,'value precision basis')
    time_value(p['value'],p['precision'])
    require(p['basis'] in ['source','upstream_unverified','unknown'])
    require((p['value'] is None)==(p['basis']=='unknown'))

def validate_request(request):
    fields(request,'contract_version request_id records')
    require(request['contract_version']=='0.2')
    string(request['request_id'],nonempty=True)
    require(isinstance(request['records'],list) and len(request['records'])<=100)
    ids=set(); contexts={}
    for r in request['records']:
        fields(r,'input_id text text_quality platform published_at collected_at timezone timezone_basis source_url source_label source_item_id course_or_project context upstream_hints')
        string(r['input_id'],nonempty=True);require(r['input_id'] not in ids);ids.add(r['input_id'])
        string(r['text']);require(len(r['text'])<=100000)
        require(r['text_quality'] in ['complete','partial','summary','missing'])
        require(r['platform'] in PLATFORMS)
        publication(r['published_at']);timezone(r['timezone'])
        if r['collected_at'] is not None: instant(r['collected_at'])
        require(r['timezone_basis'] in ['source','caller_default','unknown'])
        require((r['timezone'] is None)==(r['timezone_basis']=='unknown'))
        for key in ['source_url','source_label','source_item_id','course_or_project']: string(r[key],nullable=True)
        require(r['upstream_hints'] is None or isinstance(r['upstream_hints'],dict))
        require(isinstance(r['context'],list))
        local=set()
        for c in r['context']:
            fields(c,'input_id text published_at timezone platform source_url source_label')
            string(c['input_id'],nonempty=True);string(c['text']);publication(c['published_at']);timezone(c['timezone'])
            require(c['platform'] in PLATFORMS)
            for key in ['source_url','source_label']: string(c[key],nullable=True)
            require(c['input_id'] not in local);local.add(c['input_id'])
            require(c['input_id'] not in contexts or contexts[c['input_id']]==c)
            contexts[c['input_id']]=c
    require(not ids.intersection(contexts))

def validate_candidate(c,r):
    fields(c,'candidate_id input_id title description prerequisites course_or_project course_basis notice_type target_reference deadline needs_review review_reasons sources evidence')
    string(c['candidate_id'],nonempty=True);require(c['input_id']==r['input_id'])
    string(c['title'],nonempty=True);string(c['description']);strings(c['prerequisites'])
    string(c['course_or_project'],nullable=True);string(c['target_reference'],nullable=True)
    require(c['course_basis'] in ['provided','inferred','unknown'])
    require((c['course_or_project'] is None)==(c['course_basis']=='unknown'))
    if c['course_basis']=='provided': require(c['course_or_project']==r['course_or_project'])
    require(c['notice_type'] in ['create','update','postpone','cancel','uncertain'])
    require(type(c['needs_review']) is bool);strings(c['review_reasons'])
    require(c['needs_review']==bool(c['review_reasons']))
    d=c['deadline'];fields(d,'value precision timezone kind status raw basis alternatives')
    time_value(d['value'],d['precision']);timezone(d['timezone'])
    require(d['kind'] in ['deadline','reference','unknown'])
    require(d['status'] in ['explicit','inferred','unknown','conflict','not_provided'])
    string(d['raw'],nullable=True);string(d['basis'],nonempty=True);strings(d['alternatives'])
    if d['status'] in ['explicit','inferred','conflict']: string(d['raw'],nonempty=True)
    if d['status'] in ['unknown','conflict','not_provided']: require(d['value'] is None)
    else: require(d['value'] is not None and d['kind']!='unknown')
    if d['status']=='not_provided':
        require(r['text_quality']=='complete' and d['kind']=='unknown' and d['raw'] is None)
    if d['status']!='conflict': require(not d['alternatives'])
    if d['kind']=='reference': require(d['status']=='inferred')
    if d['status']=='explicit': require(d['kind']=='deadline')
    if d['precision']=='datetime':
        require(d['timezone'] is not None)
        dt=instant(d['value']);require(dt.astimezone(ZoneInfo(d['timezone'])).utcoffset()==dt.utcoffset())
    uncertain=d['status'] in ['unknown','conflict','inferred'] or r['text_quality'] in ['partial','summary'] or c['course_basis']=='inferred' or c['notice_type']=='uncertain'
    if uncertain: require(c['needs_review'])
    sources={x['input_id']:x for x in [r]+r['context']}
    if d['raw'] is not None:
        raw_strings=[x['text'] for x in sources.values()]
        def collect(v):
            if isinstance(v,str): raw_strings.append(v)
            elif isinstance(v,dict):
                for x in v.values(): collect(x)
        collect((r['upstream_hints'] or {}).get('raw',{}))
        require(any(d['raw'] in x for x in raw_strings))
    require(isinstance(c['sources'],list) and bool(c['sources']))
    for s in c['sources']:
        fields(s,'input_id platform url label source_item_id');require(s['input_id'] in sources)
        original=sources[s['input_id']]
        require(s['platform']==original['platform'] and s['url']==original['source_url'] and s['label']==original['source_label'] and s['source_item_id']==original.get('source_item_id'))
    require(isinstance(c['evidence'],list) and bool(c['evidence']))
    supported=set()
    for e in c['evidence']:
        fields(e,'input_id field quote supports');require(e['input_id'] in sources)
        string(e['quote'],nonempty=True);strings(e['supports'])
        require(bool(e['supports']) and len(set(e['supports']))==len(e['supports']) and set(e['supports'])<=SUPPORTS)
        string(e['field'])
        require(e['field']=='text' or (e['input_id']==r['input_id'] and e['field'].startswith('upstream_hints.raw.')))
        value=sources[e['input_id']]
        for key in e['field'].split('.'):
            require(isinstance(value,dict) and key in value);value=value[key]
        require(isinstance(value,str) and e['quote'] in value);supported.update(e['supports'])
    needed={'title'}
    if d['status'] not in ['unknown','not_provided']: needed.add('deadline')
    if c['notice_type']!='create': needed.add('notice_type')
    if c['target_reference'] is not None: needed.add('target_reference')
    if c['course_basis']=='inferred': needed.add('course_or_project')
    if c['prerequisites']: needed.add('prerequisites')
    require(needed<=supported)

def validate_result(request,result):
    validate_request(request)
    fields(result,'contract_version request_id status record_results tasks errors')
    require(result['contract_version']=='0.2' and result['request_id']==request['request_id'])
    require(all(isinstance(result[k],list) for k in ['record_results','tasks','errors']))
    records={r['input_id']:r for r in request['records']}
    for error in result['errors']:
        fields(error,'input_id code message retryable')
        require(error['input_id'] is None or error['input_id'] in records)
        string(error['code'],nonempty=True);string(error['message']);require(type(error['retryable']) is bool)
    if any(e['input_id'] is None for e in result['errors']):
        require(result['status']=='failed' and not result['tasks'] and not result['record_results'])
        return result
    require(len(result['record_results'])==len(records))
    rr={r['input_id']:r for r in result['record_results']}
    require(len(rr)==len(records) and set(rr)==set(records))
    ids=[c['candidate_id'] for c in result['tasks']]
    require(len(set(ids))==len(ids))
    require(all(c['input_id'] in records for c in result['tasks']))
    output=copy.deepcopy(result)
    for record_result in output['record_results']:
        fields(record_result,'input_id status task_ids warnings');strings(record_result['task_ids']);strings(record_result['warnings'])
        rid=record_result['input_id'];r=records[rid]
        candidates=[c for c in output['tasks'] if c['input_id']==rid]
        require(record_result['task_ids']==[c['candidate_id'] for c in candidates])
        require(record_result['status'] in ['success','no_tasks','failed'])
        require((record_result['status']=='success')==bool(candidates))
        require((record_result['status']=='failed')==any(e['input_id']==rid for e in output['errors']))
        try:
            if not r['text'].strip() or r['text_quality']=='missing': require(record_result['status']=='failed')
            for c in candidates: validate_candidate(c,r)
        except (ValueError,TypeError,KeyError):
            output['tasks']=[c for c in output['tasks'] if c['input_id']!=rid]
            output['errors']=[e for e in output['errors'] if e['input_id']!=rid]+[dict(input_id=rid,code='invalid_agent_output',message='该记录的候选未通过契约校验',retryable=True)]
            record_result.update(status='failed',task_ids=[])
        if r['text_quality'] in ['partial','summary'] and not record_result['warnings']:
            record_result['warnings']=['仅基于已提供的部分正文或摘要识别']
    failed=sum(r['status']=='failed' for r in output['record_results'])
    derived='failed' if failed and failed==len(records) else 'partial_success' if failed else 'success' if output['tasks'] else 'no_tasks'
    # A bad individual candidate can downgrade its record; malformed aggregation otherwise fails closed.
    if output==result: require(result['status']==derived)
    output['status']=derived
    return output
