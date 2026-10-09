"""#110 public operation envelopes; authentication/storage remain server-owned."""
from copy import deepcopy
import hashlib
import json

from jsonschema import Draft202012Validator, FormatChecker

from contracts import submission_policy as policy

VERSION = 'submission-operations/0.1'
ROOT = policy.ROOT
ID = policy.ID
LABEL = dict(type='string', minLength=1, maxLength=512, pattern=r'^[^\u0000-\u001f\u007f]+$')
REVISION = dict(type='integer', minimum=0)


class OperationConflict(ValueError):
    """Map to HTTP 409; the user must refresh and explicitly confirm again."""


def array(items, maximum=100):
    return dict(type='array', items=items, maxItems=maximum)


def envelope(fields):
    return policy.obj(dict(contract_version=dict(const=VERSION), **fields))


def schema():
    """Self-contained root accepting one of five disjoint public envelopes."""
    nullable_label = dict(anyOf=[LABEL, dict(type='null')])
    entry = policy.obj(dict(record_id=ID, label=LABEL, course_or_project=nullable_label))
    detail = envelope(dict(record_id=ID, label=LABEL, course_or_project=nullable_label,
        observation=policy.schema(), action_labels=array(policy.obj(dict(action_id=ID, label=LABEL)))))
    common = dict(operation_id=ID, record_id=ID, expected_snapshot_id=ID,
                  expected_control_revision=REVISION)
    control = envelope(dict(common, kind=dict(enum=['reopen_actions', 'resume_source_completion', 'resolve_source_review']),
        action_ids=dict(array(ID), uniqueItems=True)))
    associate = envelope(dict(common, kind=dict(const='associate_actions'),
        scope=dict(enum=['submission_only', 'mixed', 'unknown']),
        links=array(policy.obj(dict(action_id=ID, relation=dict(enum=['assignment_submission', 'other', 'unknown']))))))
    roots = dict(
        directory=envelope(dict(source_account_id=ID, records=array(entry),
            next_cursor=dict(anyOf=[dict(type='string', minLength=1, maxLength=512), dict(type='null')]))),
        detail=detail, operation=dict(oneOf=[control, associate]),
        receipt=envelope(dict(operation_id=ID, record_id=ID, applied_control_revision=REVISION,
            record=detail)),
        error=envelope(dict(code=dict(enum=['unauthorized', 'invalid_request', 'not_found', 'conflict', 'unavailable']),
            message=LABEL)))
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', '$defs': roots,
            'oneOf': [{'$ref': '#/$defs/' + name} for name in roots]}


def root_schema(kind):
    data = schema()
    if kind not in data['$defs']:
        raise ValueError('unknown submission envelope')
    data.pop('oneOf')
    data['$ref'] = '#/$defs/' + kind
    return data


def unique(items, key):
    policy.require(len({item[key] for item in items}) == len(items), 'duplicate ' + key)


def check(kind, value):
    policy.require(len(policy.canonical(value)) <= 250_000, 'submission envelope too large')
    Draft202012Validator(root_schema(kind), format_checker=FormatChecker()).validate(value)
    if kind == 'directory':
        unique(value['records'], 'record_id')
    elif kind == 'detail':
        policy.check(value['observation'])
        unique(value['action_labels'], 'action_id')
        policy.require({item['action_id'] for item in value['action_labels']} ==
                       {item['action_id'] for item in value['observation']['actions']}, 'action labels do not match this record')
    elif kind == 'operation':
        if value['kind'] == 'associate_actions':
            unique(value['links'], 'action_id')
            policy.require(value['scope'] != 'submission_only' or all(
                item['relation'] == 'assignment_submission' for item in value['links']), 'submission-only has unrelated links')
        else:
            policy.require(bool(value['action_ids']) == (value['kind'] != 'resolve_source_review'), 'invalid action selection')
    elif kind == 'receipt':
        check('detail', value['record'])
        policy.require(value['record_id'] == value['record']['record_id'], 'receipt record mismatch')
        policy.require(value['applied_control_revision'] == value['record']['observation']['control_revision'],
                       'receipt revision mismatch')
    return value


def request_digest(operation):
    """Store under (authenticated user, operation_id), never as authorization."""
    check('operation', operation)
    return hashlib.sha256(policy.canonical(operation)).hexdigest()


def apply_operation(record, operation):
    """Plan only: caller resolves record/actions under the logged-in user first.

    Persist the returned record, audit and receipt atomically with snapshot and
    revision CAS. Replay saved idempotent receipts BEFORE calling this helper.
    This helper neither executes source completion nor changes source evidence.
    """
    check('detail', record)
    check('operation', operation)
    policy.require(record['record_id'] == operation['record_id'], 'operation belongs to another record')
    before = record['observation']
    if before['snapshot']['snapshot_id'] != operation['expected_snapshot_id']:
        raise OperationConflict('source changed; refresh')
    if before['control_revision'] != operation['expected_control_revision']:
        raise OperationConflict('control changed; refresh')
    if operation['kind'] == 'associate_actions':
        links = {item['action_id']: item['relation'] for item in operation['links']}
        policy.require(set(links) == {a['action_id'] for a in before['actions']}, 'link every current action exactly once')
        after = deepcopy(before)
        after['scope'] = operation['scope']
        for action in after['actions']:
            relation = links[action['action_id']]
            # Unchanged facts keep their existing provenance, including server_exact.
            if action['relation'] != relation:
                action.update(relation=relation, link_basis='unresolved' if relation == 'unknown' else 'user_confirmed')
        after['control_revision'] += 1
        policy.check(after)  # In particular, an association cannot silently undo a manual pause.
    else:
        internal = {key: deepcopy(value) for key, value in operation.items() if key != 'record_id'}
        internal['contract_version'] = policy.VERSION
        after = policy.apply_user_control(before, internal)['observation']
    changed = dict(deepcopy(record), observation=after)
    receipt = dict(contract_version=VERSION, operation_id=operation['operation_id'], record_id=record['record_id'],
                   applied_control_revision=after['control_revision'], record=changed)
    check('receipt', receipt)
    audit = dict(operation=deepcopy(operation), request_digest=request_digest(operation),
                 before=deepcopy(record), after=deepcopy(changed))
    return dict(record=changed, receipt=receipt, audit=audit)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'schemas/submission-operations-v0.1.schema.json'
    text = json.dumps(schema(), ensure_ascii=False, indent=2) + '\n'
    if args.check:
        policy.require(path.read_text(encoding='utf-8') == text, 'regenerate submission operations schema')
    else:
        path.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
