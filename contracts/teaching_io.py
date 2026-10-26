"""Teaching v0.4 shape, reference, trust binding and batch consistency checks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, FormatChecker
from .check_v031_examples import check_source_url
from .teaching_review_draft import validate_pair, review_codes, require

ROOT=Path(__file__).resolve().parent
SCHEMAS={name:json.loads((ROOT/f'schemas/teaching-{name}-v0.4.schema.json').read_text(encoding='utf-8'))
         for name in ('request','result')}
MAX_BYTES=2_000_000


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def snapshot_digest(entry):
    """Bind the entire captured entry, including source, text, term and references."""
    return hashlib.sha256(canonical(entry)).hexdigest()


def validate_record(record):
    assessment=dict(candidate_id='validation-only',input_id=record['input_id'],
        action=dict(state='uncertain',evidence=[]),
        deadline=dict(value=None,precision='unknown',derivation='unresolved',anchor=None,evidence=[]),
        unread_relations=[dict(ref_id=r['ref_id'],role='unknown',evidence=[])
                          for r in record['references'] if r['read_state']!='complete'],other_review_reasons=[])
    validate_pair(dict(contract_version='0.4-draft.1',record=record,assessment=assessment))


def check_shape(name,value):
    require(len(canonical(value))<=MAX_BYTES,'teaching envelope too large')
    Draft202012Validator(SCHEMAS[name],format_checker=FormatChecker()).validate(value)


def check_request(request):
    check_shape('request',request)
    seen=set()
    for entry in request['records']:
        r=entry['record'];validate_record(r)
        require(r['input_id'] not in seen,'duplicate input_id');seen.add(r['input_id'])
        if entry['source']['url'] is not None:check_source_url(entry['source']['url'])
        for field in ('label','course_or_project'):
            value=entry['source'][field]
            require(value is None or value.strip() and not any(ord(c)<32 or ord(c)==127 for c in value),'invalid source label')
    return request


def verified_set(request,digests):
    require(isinstance(digests,(set,frozenset,tuple,list)) and not isinstance(digests,str),'invalid verified snapshot set')
    require(all(isinstance(d,str) and re.fullmatch('[0-9a-f]{64}',d) for d in digests),'invalid snapshot digest')
    values=frozenset(digests)
    require(values.issubset({snapshot_digest(e) for e in request['records']}),'verified snapshot differs from input')
    return values


def aggregate_status(rows):
    bad=sum(r['status'] in ('failed','incomplete') for r in rows)
    if bad:return 'failed' if bad==len(rows) else 'partial_success'
    return 'success' if any(r['decisions'] for r in rows) else 'no_tasks'


def check_pair(request,result,*,verified_snapshot_digests=()):
    check_request(request);check_shape('result',result)
    trusted=verified_set(request,verified_snapshot_digests)
    require(result['request_id']==request['request_id'],'request identity mismatch')
    rows=result['record_results']
    model_routing = result.get('classification_mode') == 'model-v1'
    require(len(rows)==len(request['records']),'record count mismatch')
    candidates=set()
    for entry,row in zip(request['records'],rows):
        r=entry['record'];verified=snapshot_digest(entry) in trusted
        require(row['input_id']==r['input_id'] and row['snapshot_id']==r['snapshot_id'],'record identity mismatch')
        require(row['source']==entry['source'],'source metadata changed')
        status=row['status'];decisions=row['decisions']
        require(row['attempted'] or status=='failed','unattempted record cannot contain a result')
        require((row['error'] is not None)==(status=='failed'),'error/status mismatch')
        if status=='failed':require(not decisions,'failed record contains decisions')
        elif status=='success':require(bool(decisions),'successful record has no decisions')
        else:
            require(not decisions,'empty status contains decisions')
            if model_routing:
                empty=row.get('empty_decision')
                require(empty is not None and empty['status']==status and empty['reason'].strip(), 'empty decision/status mismatch')
                texts={'body':r['text'],'source_metadata':r['source_metadata_text']}
                texts.update({x['ref_id']:x['text'] for x in r['references'] if x['read_state'] in ('complete','partial')})
                for e in empty['evidence']:
                    require(e['part_id'] in texts and e['quote'].strip() and e['quote'] in texts[e['part_id']], 'invalid empty-decision evidence')
                if status=='no_tasks':require(verified and bool(empty['evidence']), 'no_tasks requires verified captured evidence')
            else:
                covered=verified and r['body_status']=='complete' and all(x['read_state']=='complete' for x in r['references'])
                require((status=='no_tasks')==covered,'unread or unverified input cannot prove no_tasks')
            require(status!='incomplete' or bool(row['warnings']),'missing incomplete warning')
        if model_routing and status in ('success','failed'):
            require(row.get('empty_decision') is None,'nonempty/failed record cannot classify an empty result')
        for decision in decisions:
            pair=decision['pair'];require(pair['record']==r,'decision rewrites source snapshot');validate_pair(pair,model_routing=model_routing)
            if decision.get('content_type', 'task') == 'notice':
                require(model_routing, 'notice requires model classification')
            cid=pair['assessment']['candidate_id'];require(cid not in candidates,'duplicate candidate_id');candidates.add(cid)
            if not model_routing:
                # Frozen legacy results remain valid; no new model run uses these gates.
                codes=review_codes(pair,source_verified=verified)
                require(set(codes).issubset(decision['review_codes']),'missing policy review codes')
            available={'body':r['text'],'source_metadata':r['source_metadata_text']}
            available.update({x['ref_id']:x['text'] for x in r['references'] if x['read_state'] in ('complete','partial')})
            rel=decision['relevance']
            if not model_routing:require(rel['state']=='current' or bool(rel['evidence']),'missing relevance evidence')
            for quote in rel['evidence']+([decision['target']] if decision['target'] is not None else []):
                require(quote['part_id'] in available and quote['quote'].strip() and quote['quote'] in available[quote['part_id']],'invalid decision quotation')
            if decision['target'] is not None:
                require(decision['notice_type']!='create' and (model_routing or decision['target']['part_id']!='source_metadata'),'invalid change target')
            route=decision['recommendation']
            require((route=='filtered')==(decision['filter_reason'] is not None),'filter reason mismatch')
            if route in ('task','filtered'):
                require(verified and not decision['review_codes'] and not decision['review_reasons'] and decision['notice_type']=='create','unsafe automatic recommendation')
            else:require(bool(decision['review_reasons']),'candidate needs a reason')
            if model_routing:
                require(set(decision['review_codes']).issubset({'model_review','source_unverified'}), 'unexpected model classification codes')
                require(('source_unverified' in decision['review_codes']) == (not verified), 'source verification mismatch')
                if route=='candidate':require(all(x.strip() for x in decision['review_reasons']), 'candidate needs concrete reason')
                if route=='task':
                    a=pair['assessment']
                    require(a['action']['state']=='supported' and a['deadline']['derivation']!='unresolved', 'task contradicts its own assessment')
                if route=='filtered':require(decision['filter_reason'].strip() and bool(pair['assessment']['action']['evidence'] or rel['evidence']), 'filtered needs reason and captured evidence')
    require(result['status']==aggregate_status(rows),'batch status mismatch')
    return result
