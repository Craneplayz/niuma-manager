"""Shared product envelope for the existing public lecture adapters."""
from jsonschema import Draft202012Validator, FormatChecker
from contracts.submission_policy import obj

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

def check(kind,value):
    Draft202012Validator(SCHEMAS[kind],format_checker=FormatChecker()).validate(value)
    if kind=='capture' and len({e['id'] for e in value['events']})!=len(value['events']):raise ValueError('duplicate public event')
    return value
