"""Internal model compilation over the approved #114 evidence-policy component.

The public opt-in batch envelope is in teaching_pipeline; this is not an HTTP route.
The caller owns authenticated snapshot resolution; JSON cannot grant trust.
"""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from contracts.teaching_review_draft import SCHEMA, review_codes, validate_pair

from .adapters import AgentError
from .classification import classify
from .extraction import MAX_DRAFT_BYTES, _object
from .runner import run_attempt


SKILL = Path(__file__).resolve().parent / 'skills/teaching-task-extraction/SKILL.md'
VERSION = SCHEMA['properties']['contract_version']['const']
REASONS = {
    'source_unverified': '来源及课程上下文尚未由后端核验。',
    'body_incomplete': '正文未完整取得，任务要求可能缺失。',
    'action_uncertain': '行动或适用对象尚不明确。',
    'deadline_unknown': '尚未取得可确定的截止要求。',
    'deadline_coverage_unknown': '未读资料可能包含截止要求。',
    'original_age_unknown': '缺少可信发布时间或适用学期，通知新旧待核实。',
    'publication_required': '相对时间缺少可信原发布时间。',
    'publication_anchor_required': '日期应优先依据原通知发布时间。',
    'year_anchor_mismatch': '日期年份与原发布时间不一致。',
    'term_date_unresolved': '学期范围内无法唯一确定该日期。',
    'history_unresolved': '存在未解决疑点，暂不自动排除这项历史行动。',
}
UNRESOLVED_TIME = {'deadline_unknown', 'deadline_coverage_unknown', 'original_age_unknown',
                   'publication_required', 'publication_anchor_required',
                   'year_anchor_mismatch', 'term_date_unresolved', 'source_unverified'}


def _object_schema(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def evidence_catalog(record):
    """Bounded contiguous source spans: models select IDs rather than recopy PDF text."""
    parts = [('body', record['text']), ('source_metadata', record['source_metadata_text'])]
    parts.extend((r['ref_id'], r['text']) for r in record['references'] if r['read_state'] in ('partial', 'complete'))
    catalog = {}
    for part_id, text in parts:
        offset = 0
        while offset < len(text):
            end = min(offset + 240, len(text))
            if end < len(text):
                boundary = text.rfind('\n', offset + 80, end)
                if boundary >= 0: end = boundary + 1
            quote = text[offset:end]
            if quote.strip(): catalog['q' + str(len(catalog))] = dict(part_id=part_id, quote=quote)
            offset = end
    return catalog


def teaching_draft_schema(record=None, *, indexed=False):
    """Internal model envelope; policy fields and limits come from #114."""
    assessment = deepcopy(SCHEMA['properties']['assessment'])
    for key in ('candidate_id', 'input_id'):
        assessment['properties'].pop(key)
        assessment['required'].remove(key)
    quote = {'$ref': '#/$defs/quote'}
    definitions = deepcopy(SCHEMA['$defs'])
    parts = ['body'] + ([r['ref_id'] for r in record['references']
                        if r['read_state'] in ('partial', 'complete')] if record is not None else [])
    if record is not None:
        definitions['quote']['properties']['part_id']['enum'] = parts
    relevance_quote = deepcopy(SCHEMA['$defs']['quote'])
    if record is not None:
        relevance_quote['properties']['part_id']['enum'] = parts + ['source_metadata']
    quote_list = dict(type='array', items=relevance_quote, maxItems=20)
    relevance = _object_schema(dict(
        state=dict(enum=['current', 'ongoing', 'historical', 'uncertain']),
        reason=dict(type='string', minLength=1, maxLength=512), evidence=quote_list))
    task = _object_schema(dict(
        title=dict(type='string', minLength=1, maxLength=512),
        description=dict(type='string', maxLength=4096),
        notice_type=dict(enum=['create', 'update', 'postpone', 'cancel', 'uncertain']),
        target=dict(anyOf=[quote, dict(type='null')]), relevance=relevance, assessment=assessment))
    schema = _object_schema(dict(tasks=dict(type='array', items=task, maxItems=50)))
    schema.update({'$schema': SCHEMA['$schema'], '$defs': definitions})
    if indexed:
        catalog = evidence_catalog(record)
        task_ids = [key for key, q in catalog.items() if q['part_id'] != 'source_metadata']
        def reference(ids):
            return _object_schema(dict(span_id=dict(type='string', enum=ids))) if ids else {'not': {}}
        schema['$defs']['quote'] = reference(task_ids)
        relevance['properties']['evidence']['items'] = reference(list(catalog))
    return schema


def _pair(record, assessment, index=0):
    token = hashlib.sha256((record['input_id'] + ':' + str(index)).encode()).hexdigest()[:24]
    return dict(contract_version=VERSION, record=deepcopy(record), assessment=dict(
        deepcopy(assessment), input_id=record['input_id'], candidate_id='teaching-draft:' + token))


def validate_teaching_record(record):
    """Validate source facts before sending anything to a model, even for zero tasks."""
    try:
        placeholder = dict(action=dict(state='uncertain', evidence=[]),
            deadline=dict(value=None, precision='unknown', derivation='unresolved', anchor=None, evidence=[]),
            unread_relations=[dict(ref_id=r['ref_id'], role='unknown', evidence=[])
                              for r in record['references'] if r['read_state'] != 'complete'],
            other_review_reasons=[])
        validate_pair(_pair(record, placeholder))
    except Exception:
        raise AgentError('invalid_input', False) from None


def _quotes(record, evidence, *, allow_metadata=False):
    available = {'body': record['text']}
    if allow_metadata:
        available['source_metadata'] = record['source_metadata_text']
    available.update({r['ref_id']: r['text'] for r in record['references']
                      if r['read_state'] in ('partial', 'complete')})
    for item in evidence:
        if (item['part_id'] not in available or not item['quote'].strip()
                or item['quote'] not in available[item['part_id']]):
            raise AgentError('invalid_agent_output', True, 'draft_evidence')


def _reasons(codes, record, assessment):
    labels = {r['ref_id']: r['label'] for r in record['references']}
    result = []
    for code in codes:
        if code == 'other_review':
            result.extend(assessment['other_review_reasons'])
        elif code.startswith(('unread_required:', 'unread_unknown:')):
            kind, ref_id = code.split(':', 1)
            result.append(('任务依赖未读内容：' if kind == 'unread_required' else '未读内容对任务的影响尚不明确：') + labels[ref_id])
        else:
            result.append(REASONS[code])
    return list(dict.fromkeys(result))


def _history(draft, pair, codes, reasons, source_verified):
    """Filter expired teaching creates; keep ongoing/change/uncertainty safeguards."""
    record, assessment = pair['record'], pair['assessment']
    deadline = assessment['deadline']
    known = deadline['value'] is not None and not UNRESOLVED_TIME.intersection(codes)
    task = dict(notice_type=draft['notice_type'], needs_review=bool(reasons), deadline=dict(
        value=deadline['value'] if known else None, precision=deadline['precision'] if known else 'unknown',
        status='explicit' if known else 'not_provided' if deadline['derivation'] == 'not_provided' else 'unknown'))
    published = record['published_at']
    publication = dict(value=None, precision='unknown', basis='unknown')
    if source_verified and published and published['basis'] == 'source':
        publication = dict(value=published['value'], basis='source',
            precision='datetime' if 'T' in published['value'].upper() else 'date')
    # This private projection is consumed by classify only; it is never emitted
    # as a v0.2 record, nor does it claim that references have been read.
    history_record = dict(contract_version='0.2', platform='teaching_web',
        text_quality=record['body_status'], published_at=publication,
        collected_at=record['collected_at'], timezone=record['timezone'])
    history_draft = dict(relevance=draft['relevance'], times=[])
    keep, extra, filtered = classify(task, history_draft, history_record, history_grace_days=0)
    if not keep and reasons:
        return 'candidate', ['history_unresolved'], [REASONS['history_unresolved']], None
    return ('filtered' if not keep else 'candidate' if reasons or extra else 'task'), [], extra, filtered


def compile_teaching_draft(raw, record, *, source_verified=False, indexed=False):
    """Return policy previews with intact pairs, never ready-to-save API tasks."""
    if type(source_verified) is not bool:
        raise AgentError('invalid_input', False)
    validate_teaching_record(record)
    stage = 'draft_json'
    try:
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_DRAFT_BYTES:
            raise ValueError('draft budget')
        envelope = json.loads(raw, object_pairs_hook=_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        stage = 'draft_schema'
        Draft202012Validator(teaching_draft_schema(record, indexed=indexed), format_checker=FormatChecker()).validate(envelope)
        if indexed:
            catalog = evidence_catalog(record)
            def expand(value):
                if isinstance(value, dict):
                    if set(value) == {'span_id'}: return deepcopy(catalog[value['span_id']])
                    return {key: expand(item) for key, item in value.items()}
                if isinstance(value, list): return [expand(item) for item in value]
                return value
            envelope = expand(envelope)
            Draft202012Validator(teaching_draft_schema(record), format_checker=FormatChecker()).validate(envelope)
        stage = 'draft_semantics'
        decisions = []
        for index, draft in enumerate(envelope['tasks'], 1):
            if not draft['title'].strip() or not draft['relevance']['reason'].strip():
                raise ValueError('empty label')
            relevance = draft['relevance']
            if relevance['state'] != 'current' and not relevance['evidence']:
                raise ValueError('relevance needs evidence')
            _quotes(record, relevance['evidence'], allow_metadata=True)
            if draft['target'] is not None:
                _quotes(record, [draft['target']])
            if draft['notice_type'] == 'create' and draft['target'] is not None:
                raise ValueError('create cannot name a change target')
            pair = _pair(record, draft['assessment'], index)
            assessment = draft['assessment']
            _quotes(record, assessment['action']['evidence'] + assessment['deadline']['evidence'])
            for relation in assessment['unread_relations']:
                _quotes(record, relation['evidence'])
            codes = review_codes(pair, source_verified=source_verified)
            reasons = _reasons(codes, record, pair['assessment'])
            if draft['notice_type'] != 'create' and draft['target'] is None:
                reasons.append('变更对象未能唯一确定，请核对原通知。')
            route, extra_codes, extra, filtered = _history(draft, pair, codes, reasons, source_verified)
            decisions.append(dict(title=draft['title'], description=draft['description'],
                notice_type=draft['notice_type'], target=deepcopy(draft['target']),
                relevance=deepcopy(relevance), pair=pair, recommendation=route,
                review_codes=codes + extra_codes, review_reasons=list(dict.fromkeys(reasons + extra)),
                filter_reason=filtered))
        return decisions
    except AgentError:
        raise
    except ValidationError:
        raise AgentError('invalid_agent_output', True, 'draft_schema') from None
    except Exception:
        raise AgentError('invalid_agent_output', True, stage) from None


def teaching_instructions(record=None, *, indexed=False):
    return SKILL.read_text(encoding='utf-8') + '\n只输出符合此内部草稿 Schema 的 JSON：\n' + json.dumps(
        teaching_draft_schema(record, indexed=indexed), ensure_ascii=False)


@dataclass
class TeachingDraftAdapter:
    client: object
    source_verified: bool = False

    def extract(self, record, settings, attempt):
        if settings.mode != 'real':
            payload = json.dumps(dict(record=record, source_verified_by_caller=self.source_verified), ensure_ascii=False)
            raw = self.client.complete_json(teaching_instructions(record), payload, settings)
            return compile_teaching_draft(raw, record, source_verified=self.source_verified)
        # complete_json avoids the old v0.2 DraftClient.generate schema.
        # All text appears once in the catalog, with source/read-state metadata
        # kept separately. Only the server can resolve IDs into exact quotations.
        metadata = deepcopy(record)
        metadata.pop('text'); metadata.pop('source_metadata_text')
        for ref in metadata['references']: ref.pop('text')
        payload = json.dumps(dict(record_metadata=metadata, evidence_catalog=evidence_catalog(record),
                                  source_verified_by_caller=self.source_verified), ensure_ascii=False)
        raw = self.client.complete_json(teaching_instructions(record, indexed=True), payload, settings)
        return compile_teaching_draft(raw, record, source_verified=self.source_verified, indexed=True)


@dataclass(frozen=True)
class TeachingPreview:
    """Internal preview only. no_action_complete is NOT permission to auto-save."""
    decisions: tuple
    no_action_complete: bool
    warnings: tuple


def process_teaching_draft(record, *, client, settings, source_verified=False):
    """One bounded attempt; callers handle retry policy and authenticated snapshots."""
    if type(source_verified) is not bool:
        raise AgentError('invalid_input', False)
    validate_teaching_record(record)
    decisions = run_attempt(TeachingDraftAdapter(client, source_verified), deepcopy(record), settings, 0)
    covered = (source_verified and record['body_status'] == 'complete'
               and all(r['read_state'] == 'complete' for r in record['references']))
    warnings = () if decisions or covered else ('未完整取得可信内容，空草稿不能视为确定无任务。',)
    return TeachingPreview(tuple(decisions), not decisions and covered, warnings)
