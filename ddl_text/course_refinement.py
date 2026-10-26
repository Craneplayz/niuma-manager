"""Classify primary material first; supporting documents cannot supply actions."""
from copy import deepcopy
from dataclasses import replace
import json
import time

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from contracts.course_facts import refinement_schema
from .adapters import AgentError
from .draft_recovery import recover
from . import diagnostics


def primary_record(record):
    value = deepcopy(record)
    value.pop('course_context', None)
    value['references'] = [r for r in value['references'] if r['kind'] != 'course_context']
    return value


def draft_from_decision(decision):
    draft = {k: deepcopy(decision[k]) for k in ('title', 'description', 'content_type', 'notice_type',
        'target', 'relevance', 'recommendation', 'title_facts', 'calendar_rule', 'time_role', 'submission_relation') if k in decision}
    draft['assessment'] = {k: deepcopy(v) for k, v in decision['pair']['assessment'].items()
                           if k not in ('input_id', 'candidate_id')}
    draft['reason'] = (decision['filter_reason'] or '；'.join(decision['review_reasons']))[:1024]
    return draft


def source_first(adapter, record, settings, attempt):
    from .teaching_draft import (teaching_draft_schema, evidence_catalog, compile_teaching_response,
                                  teaching_instructions, _quotes, _object, MAX_DRAFT_BYTES)
    started = time.monotonic()
    # Both the first call AND its correction see only the current source.
    primary = adapter.extract_primary(primary_record(record), settings, attempt, supporting_context_pending=True)[0]
    if not primary['decisions']:
        return [primary]
    drafts = [draft_from_decision(d) for d in primary['decisions']]
    slots = {'current:' + str(i): i for i, d in enumerate(drafts) if d['recommendation'] != 'filtered'}
    catalog = evidence_catalog(record)
    # Use the full catalog's IDs in both supplied actions and returned updates.
    # Repeating literal excerpts encourages copying the wrong evidence shape.
    span_ids = {(q['part_id'], q['quote']): key for key, q in catalog.items()}
    def index(value):
        if isinstance(value, dict):
            if set(value) == {'part_id', 'quote'}:
                return dict(span_id=span_ids[(value['part_id'], value['quote'])])
            return {k: index(v) for k, v in value.items()}
        if isinstance(value, list): return [index(v) for v in value]
        return value
    def compile_drafts(values):
        # The model budget applies to indexed drafts, not repeated expanded
        # excerpts. Keep the same representation when joining the two stages.
        return [compile_teaching_response(json.dumps(dict(tasks=index(values)), ensure_ascii=False), record,
                                          source_verified=adapter.source_verified, indexed=True)]
    if not slots:
        return compile_drafts(drafts)
    schema = refinement_schema(teaching_draft_schema(record, indexed=True), list(slots))
    metadata = deepcopy(record)
    metadata.pop('text'); metadata.pop('source_metadata_text')
    for ref in metadata['references']: ref.pop('text')
    frozen = [dict(slot=slot, **drafts[i]) for slot, i in slots.items()]
    payload = dict(record_metadata=metadata, evidence_catalog=catalog, current_actions=index(frozen))
    instructions = teaching_instructions(record, indexed=True).split('只输出符合此内部草稿 Schema')[0] + '''
本调用仅补充已确认的 current_actions。行动、标题、说明、类型、原文依据由前一步独立确定，不得新增、删除、合并、拆分或重分类行动。
只输出 updates，每个 slot 恰好一次。只补充确实适用于该行动的日期、教学周规则及核对理由；无补充则保持原日期及推荐值。
其他作业的编号、进度、完成或无需提交均不能证明当前行动失效。明确取消/相互冲突的旁证用 candidate 并写明理由，不能静默过滤。
不能用无关上下文清除当前来源明确的日期。原日期改变必须提供适用规则/更正的证据；证据不足保留原日期并 candidate。
recommendation=task 仅在当前行动、时间及规则适用性都明确时使用；reason 为空。未知行动或其他未解决核对项仍用 candidate。
current_actions 中的 recommendation/reason 是只看当前来源时的临时判断。若缺少大纲、规则或截止日期的问题已由本次资料解决，应清除过时的核对理由；不能一边填入已确定日期、一边保留“尚无日期”的理由。只有仍未解决的问题才保留 candidate。
relevance 只能为 current/ongoing/uncertain。原本 current/ongoing 的状态保持；原本 uncertain 时，可用适用于当前行动的证据解除范围/规则疑点。禁止根据低编号、其他作业进度或无提交要求推断 historical。
证据使用本次 evidence_catalog 的 span_id。输出以下 Schema 的 JSON，不输出任务草稿：
''' + json.dumps(schema, ensure_ascii=False)

    def compile_updates(raw):
        try:
            if not isinstance(raw, str) or len(raw.encode()) > MAX_DRAFT_BYTES: raise ValueError('response budget')
            envelope = json.loads(raw, object_pairs_hook=_object,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(envelope)
            updates = envelope['updates']
            if {u['slot'] for u in updates} != set(slots): raise ValueError('each current action must occur exactly once')
            def expand(value):
                if isinstance(value, dict):
                    if set(value) == {'span_id'}: return deepcopy(catalog[value['span_id']])
                    return {k: expand(v) for k, v in value.items()}
                if isinstance(value, list): return [expand(v) for v in value]
                return value
            combined = deepcopy(drafts)
            for update in expand(updates):
                i = slots[update['slot']]; target = combined[i]
                _quotes(record, update['evidence'])
                _quotes(record, update['relevance']['evidence'])
                target['assessment']['deadline'] = update['deadline']
                target.update(calendar_rule=update['calendar_rule'], time_role=update['time_role'],
                    recommendation=update['recommendation'], reason=update['reason'])
                if target['relevance']['state'] == 'uncertain':
                    if update['relevance']['state'] != 'uncertain' and not update['relevance']['evidence']:
                        raise ValueError('resolving applicability needs source evidence')
                    target['relevance'] = update['relevance']
                if (drafts[i]['assessment']['action']['state'] != 'supported'
                        or drafts[i]['notice_type'] != 'create'
                        or target['relevance']['state'] in ('uncertain', 'historical')):
                    target.update(recommendation='candidate', reason=drafts[i]['reason'] or '当前来源的行动或适用性尚需核对。')
            return compile_drafts(combined)
        except AgentError: raise
        except (ValueError, KeyError, TypeError, RecursionError, ValidationError) as cause:
            error = AgentError('invalid_agent_output', True, 'draft_schema' if isinstance(cause, ValidationError) else 'draft_semantics')
            error.model_feedback = 'Only refine the supplied slots; never return tasks or other actions. ' + str(cause).splitlines()[0][:300]
            raise error from None

    def call(current, feedback):
        return adapter.client.complete_json(instructions, json.dumps(dict(payload, **(feedback or {})), ensure_ascii=False), current)

    remaining = settings.timeout_seconds - (time.monotonic() - started)
    try:
        if remaining <= 0: raise AgentError('agent_timeout', True, 'model_timeout')
        return recover(call, compile_updates, replace(settings, timeout_seconds=remaining))
    except AgentError as error:
        # Valid primary actions survive optional enrichment failure, with an
        # explicit review requirement rather than an invented contextual date.
        diagnostics.emit('course_refinement', status='fallback', code=error.code, diagnostic=error.diagnostic)
        for i in slots.values():
            drafts[i].update(recommendation='candidate', reason='当前来源已识别；课程参考资料未能完成核对，请确认日期和适用规则。')
        return compile_drafts(drafts)
