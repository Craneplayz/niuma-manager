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
COMPLETION = dict(oneOf=[obj(dict(source_id=ID, covers=dict(IDS, minItems=1))),
    obj(dict(source_ids=dict(IDS, minItems=2), covers=dict(IDS, minItems=1)))])
GROUP = obj(dict(relation=dict(enum=['duplicate', 'combine', 'update']), disposition=dict(enum=['apply', 'review']),
                 members=dict(IDS, minItems=2), result=RESULT, reason=string(1024, 1),
                 evidence=dict(type='array', minItems=1, maxItems=24, items=obj(dict(node_id=ID, quote=string(1200, 1)))),
                 completion=dict(type='array', maxItems=24, items=COMPLETION)))
OUTPUT = dict(obj(dict(contract_version=dict(const=VERSION), context_digest=string(64, 64),
                       groups=dict(type='array', items=GROUP, maxItems=12))),
              **{'$schema': 'https://json-schema.org/draft/2020-12/schema'})
API_LIST = obj(dict(contract_version=dict(const=VERSION),
    jobs=dict(type='array',maxItems=10,items=obj(dict(id=ID,origin=string(200),
        state=dict(enum=['running','succeeded','failed','interrupted','stopped']),
        error=dict(anyOf=[dict(type='null'),string(1024)]),created_at=string(64)))),
    proposals=dict(type='array',maxItems=100,items=obj(dict(id=ID,
        state=dict(enum=['review','applied','ignored','undone']),result=GROUP,
        before=dict(type='array',maxItems=MAX_NODES,items=dict(type='object')))))))


def check_list(value):
    Draft202012Validator(API_LIST).validate(value)
    return value


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
        if not group['result']['title'].strip() or not group['reason'].strip():
            raise ValueError('empty title or reason')
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
            identities = completion_sources_for(link)
            key = tuple(sorted(identities))
            if key in completion_sources:
                raise ValueError('duplicate completion requirement')
            if any(i not in members or nodes[i]['kind'] != 'source' for i in identities):
                raise ValueError('invalid completion source')
            if not set(identities) <= cited or not set(link['covers']) <= actions:
                raise ValueError('invalid completion scope')
            completion_sources.add(key)
    return result


def completion_sources_for(link):
    """One alternative requires ALL its sources; alternatives are never unioned."""
    return link['source_ids'] if 'source_ids' in link else [link['source_id']]


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--check', action='store_true'); args = parser.parse_args()
    for name,schema in [('task-relations-v0.1',OUTPUT),('task-relations-list-v0.1',API_LIST)]:
        path = Path(__file__).parent / ('schemas/'+name+'.schema.json')
        text = json.dumps(schema, ensure_ascii=False, indent=2) + '\n'
        if args.check:
            if path.read_text(encoding='utf-8') != text: raise SystemExit('regenerate task-relations schema')
        else: path.write_text(text, encoding='utf-8')
