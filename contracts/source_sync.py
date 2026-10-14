"""Shared HTTP envelopes for durable, account-scoped source synchronization."""
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

VERSION = 'source-sync/0.1'
ACTIVE = ('queued', 'crawling', 'recognizing')
FINAL = ('succeeded', 'partial_success', 'failed', 'stopped', 'interrupted')
ITEM_STATES = ('queued', 'running', 'succeeded', 'failed', 'incomplete', 'cancelled', 'interrupted')
ID = {'type': 'string', 'minLength': 1, 'maxLength': 160, 'pattern': r'^[A-Za-z0-9_.:-]+$'}


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required,
            'additionalProperties': False}


SCHEMAS = {
    'start': obj({'contract_version': {'const': VERSION}, 'operation_id': ID,
                  'sources': {'const': ['teaching_web']}, 'consent': {'const': True}}),
    'stop': obj({'contract_version': {'const': VERSION}}),
    'retry': obj({'contract_version': {'const': VERSION}, 'operation_id': ID, 'consent': {'const': True},
                  'request_ids': {'type': 'array', 'minItems': 1, 'maxItems': 1000, 'uniqueItems': True,
                                  'items': {'type': 'string', 'minLength': 1, 'maxLength': 256}}},
                 ['contract_version', 'operation_id', 'consent']),
}
COUNT = {'type': 'integer', 'minimum': 0}
ERROR = {'anyOf': [{'type': 'null'}, obj({'code': {'type': 'string'}, 'message': {'type': 'string'}, 'retryable': {'type': 'boolean'}})]}
RECORD = obj({'request_id': {'type': 'string', 'maxLength': 256}, 'label': {'type': 'string', 'maxLength': 160},
              'course': {'type': ['string', 'null']}, 'state': {'enum': list(ITEM_STATES)}, 'added': COUNT,
              'seconds': {'type': ['number', 'null'], 'minimum': 0}, 'error': ERROR})
SCHEMAS['run'] = obj({'contract_version': {'const': VERSION}, 'run_id': ID, 'operation_id': ID,
                     'sources': {'const': ['teaching_web']}, 'trigger': {'enum': ['manual', 'scheduled', 'retry']},
                     'parent_run_id': {'anyOf': [ID, {'type': 'null'}]}, 'state': {'enum': list(ACTIVE+FINAL)},
                     'revision': COUNT, 'created_at': {'type': 'string'}, 'updated_at': {'type': 'string'},
                     'skipped': COUNT, 'records': {'type': 'array', 'items': RECORD, 'maxItems': 1000},
                     'counts': obj({key: COUNT for key in (*ITEM_STATES, 'total', 'skipped', 'added')}), 'error': ERROR})
SCHEMAS['runs'] = obj({'contract_version': {'const': VERSION}, 'runs': {'type': 'array', 'items': SCHEMAS['run'], 'maxItems': 20}})
SCHEDULE = {'contract_version': {'const': VERSION}, 'enabled': {'type': 'boolean'},
            'interval_minutes': {'type': 'integer', 'minimum': 15, 'maximum': 10080},
            'sources': {'const': ['teaching_web']}, 'revision': COUNT}
SCHEMAS['schedule_update'] = obj(dict(SCHEDULE, consent={'type': 'boolean'}))
SCHEMAS['schedule'] = obj(dict(SCHEDULE, available={'type': 'boolean'},
    next_run_at={'type': ['number','null']}, last_run_id={'anyOf': [ID, {'type': 'null'}]},
    error=ERROR))


def check(kind, value):
    try: Draft202012Validator(SCHEMAS[kind]).validate(value)
    except ValidationError as exc: raise ValueError('同步请求格式不正确') from exc
    if kind == 'runs':
        for run in value['runs']: check('run', run)
    if kind == 'run':
        records = value['records']
        expected = {state: sum(r['state']==state for r in records) for state in ITEM_STATES}
        expected.update(total=len(records), skipped=value['skipped'], added=sum(r['added'] for r in records))
        if value['counts'] != expected or len({r['request_id'] for r in records}) != len(records):
            raise ValueError('同步计数或记录身份不一致')
        if value['state'] in FINAL and any(r['state'] in ('queued','running') for r in records):
            raise ValueError('同步结束后仍有未结束记录')


def root_schema(kind):
    return dict(SCHEMAS[kind], **{'$schema':'https://json-schema.org/draft/2020-12/schema'})
