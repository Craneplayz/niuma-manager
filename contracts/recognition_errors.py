"""Public, content-free recognition failures shared by every import protocol."""
import json
from pathlib import Path

CATALOG = json.loads((Path(__file__).parent / 'recognition-errors.json').read_text(encoding='utf-8'))
DEFAULTS = {
    'invalid_request': 'invalid_input', 'invalid_input': 'invalid_input',
    'unsupported_version': 'unsupported_version', 'agent_timeout': 'model_timeout',
    'agent_unavailable': 'model_unavailable', 'invalid_agent_output': 'result_contract',
    'reader_unsupported': 'attachment_unsupported', 'file_ref_unavailable': 'attachment_unavailable',
    'file_read_failed': 'attachment_unreadable', 'limit_exceeded': 'input_limit',
    'low_content_quality': 'content_uncertain', 'incomplete_content': 'content_incomplete',
    'source_incomplete': 'source_incomplete', 'source_changed': 'source_changed',
    'model_configuration': 'model_configuration', 'teaching_model_configuration': 'model_configuration',
    'crawler_dependency': 'collection_dependency', 'collection_failed': 'collection_failed',
    'save_failed': 'save_failed', 'database_busy': 'save_failed',
}


def failure(reason):
    spec = CATALOG[reason]
    return dict(reason=reason, category=spec['category'], stage=spec['stage'])


def validate_failure(value):
    if (not isinstance(value, dict) or set(value) != {'reason', 'category', 'stage'}
            or not isinstance(value['reason'], str) or value['reason'] not in CATALOG
            or value != failure(value['reason'])):
        raise ValueError('invalid_failure_descriptor')


def describe(error, *, reason=None, detail=None):
    """Preserve legacy codes/fields; never interpolate exception/provider text."""
    result = dict(error)
    supplied = result.get('failure')
    clean_detail = safe_schema_detail(detail or result.get('diagnostic'))
    result.pop('diagnostic', None)
    if clean_detail: result['diagnostic'] = clean_detail
    if isinstance(supplied, dict):
        try:
            validate_failure(supplied)
            reason = reason or supplied['reason']
        except ValueError:
            result.pop('failure', None)
    if not isinstance(reason, str) or reason not in CATALOG:
        reason = DEFAULTS.get(result.get('code'))
    if reason in CATALOG:
        spec = CATALOG[reason]
        result.update(failure=failure(reason), message=spec['message'] + spec['action'])
    return result


def schema():
    """Generated into the strict protocol schemas by build_recognition_errors.py."""
    return dict(type='object', additionalProperties=False,
                required=['reason', 'category', 'stage'], properties={
                    'reason': dict(type='string', enum=list(CATALOG)),
                    'category': dict(type='string', enum=sorted({v['category'] for v in CATALOG.values()})),
                    'stage': dict(type='string', enum=sorted({v['stage'] for v in CATALOG.values()}))})


def safe_schema_detail(value):
    """Only trusted schema vocabulary crosses worker/API boundaries, no values."""
    words = set('properties tasks items assessment action state evidence span_id deadline value precision derivation anchor unread_relations ref_id role other_review_reasons notice_type target relevance reason title description content_type time_role submission_relation empty_decision status anyOf allOf oneOf type enum const required additionalProperties maxLength minLength maxItems minItems format pattern $defs quote deadline_quote part_id input_id course prerequisites review_reasons times normalized basis image_quality asset_id quality quality_notes locator page paragraph bbox field supports target_reference course_or_project course_basis alternatives raw timezone'.split())
    if not isinstance(value,dict) or set(value) != {'schema_path','rule'}: return None
    path, rule = value['schema_path'], value['rule']
    if not isinstance(path,str) or len(path)>512 or not isinstance(rule,str) or rule not in words: return None
    if any(token not in words and not (token.isascii() and token.isdigit() and len(token)<5) for token in path.split('/') if token): return None
    return dict(schema_path=path,rule=rule)



def diagnostic_schema():
    return dict(type='object', additionalProperties=False,
                required=['schema_path', 'rule'], properties={
                    'schema_path': dict(type='string', maxLength=512, pattern=r'^[A-Za-z0-9_$/]*$'),
                    'rule': dict(type='string', enum=['type','enum','const','required','additionalProperties',
                        'maxLength','minLength','maxItems','minItems','format','pattern','anyOf','allOf','oneOf'])})
