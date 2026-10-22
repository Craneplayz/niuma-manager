"""Internal model draft derived from the one shared v0.3.1 contract."""
import copy
import json

from jsonschema import Draft202012Validator, ValidationError
from contracts.check_v031_examples import BUNDLE, FORMATS
from .extraction import _object
from .adapters import AgentError, safe_schema_detail


GENERATED = {'candidate_id', 'input_id', 'sources', 'needs_review'}


def draft_schema():
    definitions = copy.deepcopy(BUNDLE['$defs'])
    candidate = definitions['common__candidate']
    candidate.pop('allOf')  # needs_review is computed by the adapter, never by the model.
    candidate['required'] = [key for key in candidate['required'] if key not in GENERATED]
    for key in GENERATED:
        candidate['properties'].pop(key)
    candidate['allOf'] = [{
        'if': {'properties': {'notice_type': {'const': 'create'}}},
        'then': {'properties': {'target_reference': {'type': 'null'}}},
    }]
    reasons = candidate['properties']['review_reasons']
    reasons['maxItems'] = 15  # Reserve room for adapter reasons.
    # This is the model's private draft only; the public result still uses strings.
    reasons['items'] = dict(type='object', additionalProperties=False, required=['code', 'message'], properties={
        'code': dict(type='string', enum=['year_completion', 'other'], description=(
            'year_completion 仅表示省略年份后的常规补年说明；年份冲突、旧图年代不明及其他真实疑点用 other。')),
        'message': copy.deepcopy(reasons['items']),
    })
    summary = definitions['common__file_attachment_summary']
    quality_fields = ('asset_id', 'quality', 'quality_notes')
    quality_schema = dict(type='object', additionalProperties=False, required=list(quality_fields),
                          properties={key: copy.deepcopy(summary['properties'][key]) for key in quality_fields},
                          allOf=[copy.deepcopy(rule) for rule in summary['allOf']
                                 if 'quality' in rule.get('if', {}).get('properties', {})])
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema', '$defs': definitions,
        'type': 'object', 'additionalProperties': False, 'required': ['tasks', 'image_quality'],
        'properties': {
            'tasks': {'type': 'array', 'maxItems': 100, 'items': {'$ref': '#/$defs/common__candidate'}},
            'image_quality': {'type': 'array', 'maxItems': 10, 'items': quality_schema},
        },
    }


def prompt_schema():
    # Include only reachable shared definitions rather than the entire schema bundle.
    schema = draft_schema()
    all_definitions = schema.pop('$defs')
    needed = {}

    def visit(node):
        if isinstance(node, dict):
            if '$ref' in node:
                key = node['$ref'].rsplit('/', 1)[-1]
                if key not in needed:
                    needed[key] = all_definitions[key]
                    visit(needed[key])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    schema['$defs'] = needed
    return schema


def parse_draft(raw, image_ids):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 1024 * 1024:
        raise AgentError('invalid_agent_output', False, 'output_truncated')
    try:
        draft = json.loads(raw, object_pairs_hook=_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (ValueError, TypeError, RecursionError):
        raise AgentError('invalid_agent_output', True, 'draft_json') from None
    try:
        Draft202012Validator(draft_schema(), format_checker=FORMATS).validate(draft)
    except ValidationError as exc:
        error = AgentError('invalid_agent_output', True, 'draft_schema')
        from contracts.recognition_errors import validation_detail
        error.detail = validation_detail(exc)
        raise error from None
    qualities = draft['image_quality']
    if len(qualities) != len(image_ids) or {q['asset_id'] for q in qualities} != set(image_ids):
        raise AgentError('invalid_agent_output', True, 'draft_semantics')
    for item in qualities:
        if bool(item['quality_notes']) != (item['quality'] == 'uncertain'):
            raise AgentError('invalid_agent_output', True, 'draft_semantics')
    return draft
