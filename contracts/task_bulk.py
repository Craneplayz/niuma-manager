"""Shared envelope for atomic task and pending-new-candidate bulk actions."""
import json
import re
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def obj(properties, required=None):
    return dict(type='object', properties=properties, required=list(properties) if required is None else required,
                additionalProperties=False)


ID = dict(type='string', minLength=1, maxLength=256)
OP_ID = dict(type='string', pattern=r'^[A-Za-z0-9_-]{16,100}$')
DAY = dict(type='string', format='date', pattern=r'^\d{4}-\d{2}-\d{2}$')
EMPTY_DAY = dict(anyOf=[dict(const=''), DAY])
SNAPSHOT = obj(dict(title=dict(type='string', minLength=1, maxLength=20000),
                    notes=dict(type='string', maxLength=100000), course=dict(type='string', maxLength=20000),
                    content_type=dict(enum=['task', 'notice']), due=EMPTY_DAY,
                    dueTime=dict(type='string', pattern=r'^(?:|(?:[01]\d|2[0-3]):[0-5]\d)$'),
                    status=dict(enum=['todo', 'doing', 'done']), completedAt=EMPTY_DAY))
TASK = obj(dict(id=ID, expected=SNAPSHOT))
CANDIDATE = obj(dict(id=ID, version=dict(type='integer', minimum=1), taskId=OP_ID, expected=SNAPSHOT))
COMMON = dict(kind=dict(const='bulk'), workspaceGeneration=dict(type='integer', minimum=0))
ACTION = obj(dict(COMMON, action=dict(enum=['complete', 'reopen', 'deadline', 'trash', 'restore']),
                  tasks=dict(type='array', items=TASK, maxItems=500),
                  candidates=dict(type='array', items=CANDIDATE, maxItems=500),
                  date=EMPTY_DAY, completedAt=DAY,
                  deletedAt=dict(type='string', format='date-time')),
             ['kind', 'action', 'workspaceGeneration', 'tasks', 'candidates'])
UNDO = obj(dict(COMMON, action=dict(const='undo'), undoOf=OP_ID))
INTENT = dict(oneOf=[ACTION, UNDO])
OPERATION = obj(dict(id=OP_ID, owner=ID, intent=INTENT))
REQUEST = obj(dict(version=dict(type='integer', minimum=0), operation=OPERATION))
RESPONSE = obj(dict(version=dict(type='integer', minimum=0), value=dict(type='array', items=dict(type='object')),
                    operationId=OP_ID, undo=dict(anyOf=[dict(type='null'), UNDO]), replayed=dict(type='boolean')),
               ['version', 'value', 'operationId', 'undo'])
RESPONSE['properties']['batches'] = dict(type='array', items=dict(type='object'))
RESPONSE['properties']['candidates'] = dict(type='array', items=dict(type='object'))
RESPONSE['properties']['generation'] = dict(type='integer', minimum=0)
RESPONSE['required'].append('batches')
SCHEMAS = dict(intent=INTENT, operation=OPERATION, request=REQUEST, response=RESPONSE)


def check(kind, value):
    Draft202012Validator(SCHEMAS[kind], format_checker=FormatChecker()).validate(value)
    intent = value if kind == 'intent' else value.get('intent') if kind == 'operation' else value.get('operation', {}).get('intent')
    if intent and intent['action'] != 'undo':
        tasks, candidates = intent['tasks'], intent['candidates']
        if not 1 <= len(tasks) + len(candidates) <= 500:
            raise ValueError('bulk selection must contain 1–500 entries')
        for entries in (tasks, candidates):
            if len({entry['id'] for entry in entries}) != len(entries):
                raise ValueError('duplicate bulk selection')
        if len({entry['taskId'] for entry in candidates}) != len(candidates):
            raise ValueError('duplicate new task ID')
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', entry['taskId']) for entry in candidates):
            raise ValueError('invalid new task ID')
        if set(entry['id'] for entry in tasks) & set(entry['taskId'] for entry in candidates):
            raise ValueError('new task ID overlaps selection')
        if candidates and intent['action'] not in ('complete', 'deadline', 'trash'):
            raise ValueError('pending candidates cannot be reopened or restored as tasks')
        required = {'complete': 'completedAt', 'deadline': 'date', 'trash': 'deletedAt'}.get(intent['action'])
        if required and required not in intent:
            raise ValueError('missing bulk action value')
        for entry in tasks + candidates:
            expected = entry['expected']
            if expected['dueTime'] and not expected['due']:
                raise ValueError('time requires a date')
    return value


def bundle():
    definitions = deepcopy(SCHEMAS)
    definitions.update(snapshot=deepcopy(SNAPSHOT), task=deepcopy(TASK), candidate=deepcopy(CANDIDATE),
                       action=deepcopy(ACTION), undo=deepcopy(UNDO))
    for name in ('task', 'candidate'):
        definitions[name]['properties']['expected'] = {'$ref': '#/$defs/snapshot'}
    for key, name in (('tasks', 'task'), ('candidates', 'candidate')):
        definitions['action']['properties'][key]['items'] = {'$ref': '#/$defs/' + name}
    definitions['intent'] = {'oneOf': [{'$ref': '#/$defs/action'}, {'$ref': '#/$defs/undo'}]}
    definitions['operation']['properties']['intent'] = {'$ref': '#/$defs/intent'}
    definitions['request']['properties']['operation'] = {'$ref': '#/$defs/operation'}
    definitions['response']['properties']['undo'] = {'anyOf': [{'type': 'null'}, {'$ref': '#/$defs/undo'}]}
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', '$defs': definitions,
            'anyOf': [{'$ref': '#/$defs/' + name} for name in SCHEMAS]}


if __name__ == '__main__':
    import sys
    path = Path(__file__).parent / 'schemas/task-bulk-v0.1.schema.json'
    text = json.dumps(bundle(), ensure_ascii=False, indent=2) + '\n'
    if '--check' in sys.argv:
        assert path.read_text(encoding='utf-8') == text, 'regenerate task-bulk schema'
    else:
        path.write_text(text, encoding='utf-8')
