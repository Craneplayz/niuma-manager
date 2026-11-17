"""Course-scoped, source-backed context shared by producer and recognizer.

Context is reference material, not an instruction or an additional task source.
The authenticated server selects it; model output cannot grant source trust.
"""
import hashlib
import json

VERSION = 'course-context/0.1'
MAX_DOCUMENTS = 12
MAX_CHARACTERS = 24000


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def scope(record):
    """Require a captured semester; do not infer a semester from collection time."""
    term = record.get('course_term')
    if not term or not term.get('term_id'):
        return None
    return record['course_id'], term['term_id']


def revision(documents):
    return hashlib.sha256(canonical(documents)).hexdigest()


def validate_context(record):
    context = record.get('course_context')
    if context is None:
        if any(ref['kind'] == 'course_context' for ref in record['references']):
            raise ValueError('course context provenance is missing')
        return
    if context['contract_version'] != VERSION or scope(record) is None:
        raise ValueError('course context requires an explicit course and semester')
    refs = {ref['ref_id']: ref for ref in record['references']}
    seen = set()
    for document in context['documents']:
        rid = document['ref_id']
        if rid in seen or rid not in refs or refs[rid]['kind'] != 'course_context':
            raise ValueError('invalid course context reference')
        seen.add(rid)
        if (document['course_id'], document['term_id']) != scope(record):
            raise ValueError('course context crosses course or semester')
        if document['input_id'] == record['input_id']:
            raise ValueError('course context duplicates the current source')
        if refs[rid]['read_state'] not in ('complete', 'partial'):
            raise ValueError('course context requires readable evidence')
        if document['length'] != len(refs[rid]['text']):
            raise ValueError('course context span length differs from evidence')
        if document['source_url'] is not None:
            from .check_v031_examples import check_source_url
            check_source_url(document['source_url'])
    if seen != {ref['ref_id'] for ref in refs.values() if ref['kind'] == 'course_context'}:
        raise ValueError('course context provenance is missing')
    if sum(len(refs[rid]['text']) for rid in seen) > MAX_CHARACTERS:
        raise ValueError('course context exceeds its budget')
    material = [dict(document, text=refs[document['ref_id']]['text']) for document in context['documents']]
    if context['revision'] != revision(material):
        raise ValueError('course context revision does not match evidence')
