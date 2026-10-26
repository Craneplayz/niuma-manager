"""Shared product envelope for the existing public lecture adapters."""
from jsonschema import Draft202012Validator, FormatChecker
from contracts.submission_policy import obj
import json
from pathlib import Path

VERSION='public-import/0.1'
SOURCES={'cs':'计算机学院讲座','bio':'生命科学学院活动','gse':'教育学院活动'}
ID={'type':'string','minLength':1,'maxLength':160,'pattern':r'^[A-Za-z0-9_.:-]+$'}
TEXT={'type':'string','maxLength':20000}
DATE={'anyOf':[{'type':'null'},{'type':'string','format':'date-time'}]}
EVENT=obj(dict(id=ID,title=dict(TEXT,minLength=1,maxLength=512),description=TEXT,start_time=DATE,end_time=DATE,
              location={'type':['string','null'],'maxLength':512},url={'type':'string','format':'uri','maxLength':2048},source_id={'enum':list(SOURCES)}))
SCHEMAS={
 'capture':obj(dict(contract_version={'const':VERSION},events={'type':'array','items':EVENT,'maxItems':100},pages_failed={'type':'integer','minimum':0})),
 'start':obj(dict(contract_version={'const':VERSION},operation_id=ID,source_id={'enum':list(SOURCES)})),
 'import':obj(dict(contract_version={'const':VERSION},event_ids={'type':'array','items':ID,'uniqueItems':True,'minItems':1,'maxItems':100},tasks_version={'type':'integer','minimum':0})),
}
RUN=obj(dict(contract_version={'const':VERSION},run_id=ID,source_id={'enum':list(SOURCES)},
             state={'enum':['running','succeeded','failed']},
             result={'anyOf':[{'type':'null'},SCHEMAS['capture']]},error={'type':['string','null'],'maxLength':512},
             created_at={'type':'string','format':'date-time'}))
SCHEMAS.update(run=RUN,
    catalog=obj(dict(contract_version={'const':VERSION},sources={'type':'array','items':obj(dict(id={'enum':list(SOURCES)},label=dict(TEXT,maxLength=512))),'maxItems':3})),
    runs=obj(dict(contract_version={'const':VERSION},runs={'type':'array','items':RUN,'maxItems':10})),
    imported=obj(dict(added={'type':'array','items':ID,'uniqueItems':True,'maxItems':100},already_imported={'type':'integer','minimum':0,'maximum':100})))

def check(kind,value):
    Draft202012Validator(SCHEMAS[kind],format_checker=FormatChecker()).validate(value)
    if kind=='capture' and len({e['id'] for e in value['events']})!=len(value['events']):raise ValueError('duplicate public event')
    if kind=='run':
        if (value['state']=='succeeded')!=(value['result'] is not None):raise ValueError('result/state mismatch')
        if (value['state']=='failed')!=(value['error'] is not None):raise ValueError('error/state mismatch')
        if value['result'] is not None:
            check('capture',value['result'])
            if any(e['source_id']!=value['source_id'] for e in value['result']['events']):raise ValueError('event/source mismatch')
    if kind=='runs':
        if len({r['run_id'] for r in value['runs']})!=len(value['runs']):raise ValueError('duplicate run')
        for run in value['runs']:check('run',run)
    return value


def bundle():
    return {'$schema':'https://json-schema.org/draft/2020-12/schema', '$defs':SCHEMAS,
            'anyOf':[{'$ref':'#/$defs/'+name} for name in SCHEMAS]}


if __name__=='__main__':
    import sys
    path=Path(__file__).parent/'schemas/public-import-v0.1.schema.json'
    text=json.dumps(bundle(),ensure_ascii=False,indent=2)+'\n'
    if '--check' in sys.argv:
        assert path.read_text(encoding='utf-8')==text, 'regenerate public-import schema'
    else:path.write_text(text,encoding='utf-8')
