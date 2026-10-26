"""Evidence-bearing relation proposals; validation never authorizes a merge."""
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

VERSION = 'relation-foundation/0.1'
KINDS = ('duplicate', 'complement', 'bundle', 'independent')
ID = dict(type='string', minLength=1, maxLength=256)
HASH = dict(type='string', pattern='^[0-9a-f]{64}$')
TEXT = dict(type='string', maxLength=24000)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def identity(kind, *scope):
    return kind + ':' + digest(list(scope))


def obj(**fields):
    return dict(type='object', properties=fields, required=list(fields), additionalProperties=False)


def arr(item, maximum=100, minimum=0):
    return dict(type='array', items=item, maxItems=maximum, minItems=minimum)


def nullable(item):
    return dict(anyOf=[item, dict(type='null')])


def schema():
    quote = obj(source_id=ID, part_id=ID, part_revision=HASH,
                offset=dict(type='integer', minimum=0), quote=dict(type='string', minLength=1, maxLength=4000))
    evidence = arr(quote, 30, 1)
    submission = obj(state=dict(enum=['active', 'submitted', 'unknown']), basis=ID,
                     snapshot_id=ID, sequence=dict(type='integer', minimum=0),
                     control_revision=dict(type='integer', minimum=0), review_pending=dict(type='boolean'),
                     scope=dict(enum=['submission_only', 'mixed', 'unknown']), control_digest=HASH)
    part = obj(part_id=ID, label=dict(type='string', maxLength=1024), text=TEXT, read_state=dict(enum=['complete', 'partial', 'unread', 'unsupported', 'failed']),
               revision=HASH, sha256=nullable(HASH))
    source = obj(id=ID, input_id=dict(type='string', minLength=1, maxLength=10000), course_id=ID,
                 content_id=nullable(ID), term_id=nullable(ID), label=dict(type='string', maxLength=1024),
                 course_name=nullable(dict(type='string', maxLength=1024)), url=nullable(dict(type='string', maxLength=8192)),
                 starts_on=nullable(dict(type='string', format='date')), ends_on=nullable(dict(type='string', format='date')),
                 snapshot_id=ID, revision=HASH, parts=arr(part, 100), submission=nullable(submission))
    action = obj(id=ID, kind=dict(enum=['task', 'candidate']), binding_id=ID,
                 state=dict(type='string', maxLength=64), label=dict(type='string', maxLength=1024),
                 revision=HASH, source_ids=arr(ID, 20, 1), protected=dict(type='boolean'))
    context = obj(contract_version=dict(const=VERSION), scope_id=ID, source_account_id=ID,
                  term_id=nullable(ID), sources=arr(source, 20, 1), actions=arr(action, 200),
                  transitions=arr(obj(candidate_id=ID, task_id=ID, basis=dict(const='saved_history')), 500),
                  warnings=arr(dict(type='string', maxLength=128), 100), revision=HASH)
    unit = obj(id=ID, label=dict(type='string', minLength=1, maxLength=512),
               channel=dict(type='string', maxLength=128), action_ids=arr(ID, 200), evidence=evidence)
    relation = obj(contract_version=dict(const=VERSION), context_revision=HASH,
                   kind=dict(enum=list(KINDS)), source_ids=arr(ID, 20, 2),
                   roles=arr(obj(source_id=ID, role=dict(enum=['content', 'submission', 'correction', 'reference']), evidence=evidence), 40, 2),
                   units=arr(unit, 50, 1), evidence=evidence,
                   coverage=arr(obj(source_id=ID, unit_ids=arr(ID, 50, 1),
                                    extent=dict(enum=['complete', 'partial', 'unknown']), evidence=evidence), 50),
                   deadline=nullable(obj(date=dict(type='string', format='date'),
                                         time=nullable(dict(type='string', pattern='^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')),
                                         evidence=evidence)))
    return dict({'$schema': 'https://json-schema.org/draft/2020-12/schema'},
                **{'$defs': dict(context=context, relation=relation),
                   'oneOf': [{'$ref': '#/$defs/context'}, {'$ref': '#/$defs/relation'}]})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique(values, label):
    require(len(values) == len(set(values)), 'duplicate ' + label)


def check(kind, value):
    require(len(json.dumps(value, ensure_ascii=False).encode()) <= 1_500_000, 'relation payload too large')
    root = schema()
    root.pop('oneOf')
    root['$ref'] = '#/$defs/' + kind
    Draft202012Validator(root, format_checker=FormatChecker()).validate(value)
    if kind == 'context':
        sources = {s['id']: s for s in value['sources']}
        unique([s['id'] for s in value['sources']], 'source')
        unique([a['id'] for a in value['actions']], 'action')
        for s in sources.values():
            require(s['revision'] == digest({k: v for k, v in s.items() if k != 'revision'}), 'source revision mismatch')
            unique([p['part_id'] for p in s['parts']], 'part')
            for p in s['parts']:
                require(p['revision'] == digest({k: v for k, v in p.items() if k != 'revision'}), 'part revision mismatch')
            if value['term_id'] is not None:
                require(s['term_id'] == value['term_id'], 'different semester')
            if s['submission']:
                require(s['submission']['basis'] == {'submitted': 'review_submission_history_title',
                    'active': 'active_assignment_page', 'unknown': 'unknown'}[s['submission']['state']], 'submission basis mismatch')
        actions = {a['id']: a for a in value['actions']}
        for a in actions.values():
            unique(a['source_ids'], 'action source')
            require(set(a['source_ids']) <= sources.keys(), 'unknown action source')
        pairs = []
        for link in value['transitions']:
            c, t = actions.get(link['candidate_id']), actions.get(link['task_id'])
            require(c and t and c['kind'] == 'candidate' and t['kind'] == 'task', 'invalid promotion')
            require(set(c['source_ids']) <= set(t['source_ids']), 'promotion source mismatch')
            pairs.append((c['id'], t['id']))
        unique(pairs, 'promotion')
        require(value['revision'] == digest({k: v for k, v in value.items() if k != 'revision'}), 'context revision mismatch')
    return value


def validate_relation(context, relation):
    """Validate membership and exact evidence, not the truth of semantic equivalence."""
    check('context', context)
    check('relation', relation)
    require(relation['context_revision'] == context['revision'], 'stale context')
    require(context['term_id'] is not None, 'semester unresolved')
    sources = {s['id']: s for s in context['sources']}
    members = relation['source_ids']
    unique(members, 'relation source')
    require(set(members) <= sources.keys(), 'unknown relation source')
    require(all(sources[s]['term_id'] == context['term_id'] for s in members), 'different semester')
    windows = {(sources[s]['starts_on'], sources[s]['ends_on']) for s in members}
    require(len(windows) == 1 and all(start and end and start <= end for start, end in windows), 'semester window unresolved')
    units = {u['id']: u for u in relation['units']}
    unique([u['id'] for u in relation['units']], 'unit')
    require(len(units) == 1 if relation['kind'] in ('duplicate', 'complement') else len(units) >= 2, 'invalid delivery cardinality')
    actions = {a['id']: a for a in context['actions']}
    used = []

    def quotes(items):
        for q in items:
            require(q['source_id'] in members, 'evidence outside relation')
            part = next((p for p in sources[q['source_id']]['parts'] if p['part_id'] == q['part_id']), None)
            require(part and part['read_state'] in ('complete', 'partial'), 'unread evidence')
            require(q['part_revision'] == part['revision'], 'stale evidence')
            require(part['text'][q['offset']:q['offset'] + len(q['quote'])] == q['quote'], 'quote does not match source')

    quotes(relation['evidence'])
    require({q['source_id'] for q in relation['evidence']} == set(members), 'missing member evidence')
    roles = [(r['source_id'], r['role']) for r in relation['roles']]
    unique(roles, 'role')
    require({sid for sid, _ in roles} == set(members), 'missing source role')
    for role in relation['roles']:
        quotes(role['evidence'])
        require(any(q['source_id'] == role['source_id'] for q in role['evidence']), 'role lacks own source evidence')
    for unit in units.values():
        quotes(unit['evidence'])
        for aid in unit['action_ids']:
            require(aid in actions and set(actions[aid]['source_ids']) <= set(members), 'unknown or partly external action')
            require('partial_task_provenance:' + aid not in context['warnings'], 'incomplete task provenance')
        used.extend(unit['action_ids'])
    unique(used, 'action across units')
    coverage_sources = []
    for coverage in relation['coverage']:
        sid = coverage['source_id']
        require((sid, 'submission') in roles and sources[sid]['submission'] is not None, 'not a native submission source')
        unique(coverage['unit_ids'], 'covered unit')
        require(set(coverage['unit_ids']) <= units.keys(), 'unknown covered unit')
        quotes(coverage['evidence'])
        require(any(q['source_id'] == sid for q in coverage['evidence']), 'coverage lacks submission evidence')
        coverage_sources.append(sid)
    unique(coverage_sources, 'submission coverage')
    if relation['deadline']:
        quotes(relation['deadline']['evidence'])
    return relation


def coverage_summary(context, relation):
    """Expose coverage separately from observed state; NEVER an execution permit."""
    validate_relation(context, relation)
    all_units = {u['id'] for u in relation['units']}
    sources = {s['id']: s for s in context['sources']}
    return [dict(source_id=c['source_id'], covered_unit_ids=c['unit_ids'],
                 uncovered_unit_ids=sorted(all_units - set(c['unit_ids'])), extent=c['extent'],
                 observed_state=sources[c['source_id']]['submission']['state'],
                 review_pending=sources[c['source_id']]['submission']['review_pending'],
                 application_allowed=False) for c in relation['coverage']]


if __name__ == '__main__':
    import sys
    target = Path(__file__).parent / 'schemas' / 'relation-foundation-v0.1.schema.json'
    content = json.dumps(schema(), ensure_ascii=False, indent=2) + '\n'
    if '--check' in sys.argv:
        require(target.read_text(encoding='utf-8') == content, 'generated schema differs')
    else:
        target.write_text(content, encoding='utf-8')
