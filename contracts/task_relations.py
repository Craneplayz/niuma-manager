"""Account-resolved task relationships; no semantic decisions or database writes."""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator

VERSION = 'task-relations/0.1'
MAX_NODES = 24
MAX_BYTES = 180_000


def obj(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def string(limit, minimum=0):
    return dict(type='string', minLength=minimum, maxLength=limit)


ID = string(160, 1)
IDS = dict(type='array', items=ID, uniqueItems=True, maxItems=MAX_NODES)
DUE = dict(anyOf=[dict(type='null'), obj(dict(date=string(10, 10), time=dict(anyOf=[dict(type='null'), string(5, 5)])))])
RESULT = obj(dict(title=string(512, 1), description=string(4096), course=dict(anyOf=[dict(type='null'), string(512)]),
                  content_type=dict(enum=['task', 'notice']), due=DUE))
GROUP = obj(dict(relation=dict(enum=['duplicate', 'combine', 'update']), disposition=dict(enum=['apply', 'review']),
                 members=dict(IDS, minItems=2), result=RESULT, reason=string(1024, 1),
                 evidence=dict(type='array', minItems=1, maxItems=24, items=obj(dict(node_id=ID, quote=string(1200, 1)))),
                 completion=dict(type='array', maxItems=24, items=obj(dict(source_id=ID, covers=dict(IDS, minItems=1))))))
OUTPUT = dict(obj(dict(contract_version=dict(const=VERSION), context_digest=string(64, 64),
                       groups=dict(type='array', items=GROUP, maxItems=12))),
              **{'$schema': 'https://json-schema.org/draft/2020-12/schema'})


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def context(nodes):
    """Caller must resolve these nodes from one authenticated account."""
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES:
        raise ValueError('invalid context size')
    ids = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or not node['id'] or len(node['id']) > 160:
            raise ValueError('invalid node identity')
        if node['id'] in ids or node.get('kind') not in ('task', 'candidate', 'source'):
            raise ValueError('duplicate node or invalid kind')
        if not isinstance(node.get('text'), str) or len(node['text']) > 20_000:
            raise ValueError('invalid node text')
        ids.add(node['id'])
    value = dict(contract_version=VERSION, nodes=deepcopy(nodes))
    if len(canonical(value)) > MAX_BYTES:
        raise ValueError('context too large')
    return dict(value, context_digest=digest(value))


def check_due(value):
    if value is None:
        return
    if date.fromisoformat(value['date']).isoformat() != value['date']:
        raise ValueError('invalid date')
    if value['time'] is not None:
        if datetime.strptime(value['time'], '%H:%M').strftime('%H:%M') != value['time']:
            raise ValueError('invalid time')


def check_pair(request, result):
    """Only shape, exact references and scope. Similarity is never checked here."""
    if context(request['nodes']) != request:
        raise ValueError('context digest mismatch')
    if len(canonical(result)) > MAX_BYTES:
        raise ValueError('result too large')
    Draft202012Validator(OUTPUT).validate(result)
    if result['context_digest'] != request['context_digest']:
        raise ValueError('stale context')
    nodes = {n['id']: n for n in request['nodes']}
    used = set()
    for group in result['groups']:
        members = set(group['members'])
        if not members <= nodes.keys():
            raise ValueError('unknown relation member')
        actions = {i for i in members if nodes[i]['kind'] != 'source'}
        if not actions or used.intersection(actions):
            raise ValueError('missing or overlapping actions')
        used.update(actions)
        check_due(group['result']['due'])
        cited = set()
        for quote in group['evidence']:
            identity = quote['node_id']
            if identity not in members or quote['quote'] not in nodes[identity]['text']:
                raise ValueError('unknown or fabricated evidence')
            cited.add(identity)
        # Every merged action needs an actual cited contribution. This does not
        # decide whether the quotation semantically supports that relationship.
        if not actions <= cited:
            raise ValueError('uncited action')
        completion_sources = set()
        for link in group['completion']:
            identity = link['source_id']
            if identity in completion_sources or identity not in members or nodes[identity]['kind'] != 'source':
                raise ValueError('invalid completion source')
            if identity not in cited or not set(link['covers']) <= actions:
                raise ValueError('invalid completion scope')
            completion_sources.add(identity)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--check', action='store_true'); args = parser.parse_args()
    path = Path(__file__).parent / 'schemas/task-relations-v0.1.schema.json'
    text = json.dumps(OUTPUT, ensure_ascii=False, indent=2) + '\n'
    if args.check:
        if path.read_text(encoding='utf-8') != text: raise SystemExit('regenerate task-relations schema')
    else: path.write_text(text, encoding='utf-8')
