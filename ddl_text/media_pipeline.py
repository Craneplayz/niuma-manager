"""Opt-in v0.3.1 recognition; the caller owns authentication, storage and jobs."""
import base64
import copy
import hashlib
import json
import math
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from jsonschema import ValidationError
from contracts.check_v031_examples import check_request, check_pair, json_bytes
from contracts.check_v03_examples import coverage
from .adapters import AgentError
from .config import Settings
from .image_reader import IMAGE_MIMES, READER_VERSION, ImageLimits, MetadataMismatch, read_image
from .media_schema import parse_draft, prompt_schema
from .media_worker import bounded
from .time_validation import _nearest_year


class AssetLoader(Protocol):
    def __call__(self, file_ref: str, *, max_bytes: int, timeout_seconds: float) -> bytes:
        """Trusted callback bound to the authenticated account, NOT input user_id.

        Recheck ownership/revocation on every call, enforce both bounds, return
        at most max_bytes bytes; raise FileNotFoundError for inaccessible refs.
        Never interpret the opaque reference as a path or arbitrary URL.
        """
        ...


class MediaInputError(ValueError):
    """Safe rejection code for the consumer to map to an HTTP/job error."""


@dataclass(frozen=True)
class MediaLimits:
    images: ImageLimits = field(default_factory=ImageLimits)
    record_seconds: float = 70
    batch_seconds: float = 180
    model_input_bytes: int = 16 * 1024 * 1024

    def __post_init__(self):
        for value in (self.record_seconds, self.batch_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 600:
                raise ValueError('invalid_media_timeout')
        if type(self.model_input_bytes) is not int or not 0 < self.model_input_bytes <= 32 * 1024 * 1024:
            raise ValueError('invalid_model_input_limit')


MESSAGES = {
    'reader_unsupported': '本读取器仅支持静态 JPG/PNG；其他文件格式尚未开放。',
    'file_ref_unavailable': '附件不可读取，请恢复文件或重新上传。',
    'file_read_failed': '附件读取未完成，请检查文件后重试。',
    'limit_exceeded': '附件或本次处理超过限制，请拆分或减小输入。',
    'agent_timeout': '图片识别超时，可稍后重试。',
    'agent_unavailable': '图片识别服务暂不可用。',
    'invalid_agent_output': '模型结果未通过格式或证据校验。',
    'low_content_quality': '图片质量不足以确定是否包含任务，请查看原件。',
    'incomplete_content': '部分内容未完整读取，不能确定是否还有任务或截止日期。',
}
RESULT_MAX_BYTES = 2_000_000  # Shared check_pair transport bound, including aggregate candidates.


def _error(record, code, stage, asset_id=None, retryable=False):
    return dict(input_id=record['input_id'], asset_id=asset_id, block_id=None,
                stage=stage, code=code, message=MESSAGES[code], retryable=retryable)


def _file_summary(asset, status='failed'):
    return dict(asset_id=asset['asset_id'], content_mode='file', status=status,
                reader_version=READER_VERSION, coverage_basis='unverified',
                coverage=dict(complete=False, unread_locations=['整张图片或文件尚未读取'], truncated=False),
                quality='normal', quality_notes=[])


def _mark_complete(summary):
    summary.update(status='complete', coverage_basis='adapter_confirmed',
                   coverage=dict(complete=True, unread_locations=[], truncated=False))


def _model_call(client, instructions, parts, settings):
    return client.complete_json(instructions, parts, settings)


def _instructions():
    rules = (Path(__file__).parent / 'skills/image-task-extraction/SKILL.md').read_text(encoding='utf-8')
    return rules + '\n只返回符合下列内部草稿 Schema 的 JSON；不输出 Markdown：\n' + json.dumps(prompt_schema(), ensure_ascii=False)


def _prepare(record, loader, limits, expires):
    summaries, errors, pictures = [], [], []
    size = 0
    for asset in record['attachments']:
        if asset.get('content_mode') != 'file':
            summaries.append(dict(asset_id=asset['asset_id'], status=asset['status'], block_count=len(asset['blocks']),
                                  uncertain_block_ids=[b['block_id'] for b in asset['blocks'] if b['quality'] == 'uncertain']))
        else:
            summary = _file_summary(asset, asset['status'] if asset['status'] != 'available' else 'failed')
            summaries.append(summary)
        for error in asset['errors']:
            errors.append(dict(input_id=record['input_id'], asset_id=asset['asset_id'], block_id=None, **error))
        if asset.get('content_mode') != 'file' or asset['status'] != 'available':
            continue
        try:
            if asset['mime_type'] not in IMAGE_MIMES:
                summary['status'] = 'unsupported'
                raise AgentError('reader_unsupported', False)
            remaining = expires - time.monotonic()
            if remaining <= 0:
                raise AgentError('file_read_failed', True)
            if asset['size_bytes'] > limits.images.max_bytes:
                raise AgentError('limit_exceeded', False)
            try:
                data = loader(asset['file_ref'], max_bytes=limits.images.max_bytes,
                              timeout_seconds=min(remaining, limits.images.decode_seconds))
            except FileNotFoundError:
                raise AgentError('file_ref_unavailable', False) from None
            except (TimeoutError, OSError):
                raise AgentError('file_read_failed', True) from None
            except AgentError:
                raise
            except Exception:
                raise AgentError('file_read_failed', False) from None
            remaining = expires - time.monotonic()
            if remaining <= 0:
                raise AgentError('file_read_failed', True)
            png = read_image(data, asset, replace(limits.images, decode_seconds=min(remaining, limits.images.decode_seconds)))
            encoded = base64.b64encode(png).decode('ascii')
            if size + len(encoded) > limits.model_input_bytes:
                raise AgentError('limit_exceeded', False)
            size += len(encoded)
            pictures.append((asset['asset_id'], encoded))
        except MetadataMismatch:
            raise  # An immutable stored file did not match its authorized receipt.
        except AgentError as error:
            code = error.code if error.code in ('reader_unsupported', 'limit_exceeded', 'file_ref_unavailable', 'file_read_failed') else 'file_read_failed'
            if code == 'reader_unsupported':
                summary['status'] = 'unsupported'
            errors.append(_error(record, code, 'read', asset['asset_id'], error.retryable))
    return summaries, errors, pictures


def _parts(record, summaries, pictures, errors):
    source = copy.deepcopy(record)
    # Only the consumer may resolve references; neither model nor output sees a storage handle.
    for asset in source['attachments']:
        for key in ('file_ref', 'sha256', 'size_bytes'):
            asset.pop(key, None)
    payload = dict(record=source, reader=dict(sent_image_ids=[p[0] for p in pictures], errors=errors))
    parts = [dict(type='text', text=json.dumps(payload, ensure_ascii=False))]
    for asset_id, encoded in pictures:
        parts.extend([dict(type='text', text=json.dumps(dict(asset_id=asset_id))),
                      dict(type='image_url', image_url=dict(url='data:image/png;base64,' + encoded))])
    return parts


def _routine_year_completion(task, record, cov):
    """Check normalized dates against their own sources, never parse source wording.

    The model classifies the reason. Source metadata and the existing nearest-year
    policy establish whether that classification can suppress a redundant message.
    No deadline, evidence, or mandatory image review flag is changed here.
    """
    deadline = task['deadline']
    if (not cov['complete'] or cov['uncertain'] or deadline['status'] != 'explicit'
            or deadline['kind'] != 'deadline' or deadline['alternatives']
            or deadline['precision'] not in ('date', 'datetime')):
        return False
    sources = {s['input_id']: s for s in [record] + record['context']}
    assets = {a['asset_id']: a for a in record['attachments']}
    evidence = [e for e in task['evidence'] if 'deadline' in e['supports']]
    if not evidence:
        return False
    try:
        target = date.fromisoformat(deadline['value'][:10])
        for item in evidence:
            source = sources[item['input_id']]
            if item['field'] in ('attachment_file', 'attachment'):
                if item['input_id'] != record['input_id']:
                    return False
                publication = assets[item['asset_id']]['original_published_at']
            elif item['field'] == 'text':
                publication = source['published_at']
            else:
                return False
            if publication['basis'] != 'source':
                return False
            if publication['precision'] == 'date':
                anchor = date.fromisoformat(publication['value'])
            elif publication['precision'] == 'datetime' and source['timezone'] is not None:
                anchor = datetime.fromisoformat(publication['value'].replace('Z', '+00:00')).astimezone(
                    ZoneInfo(source['timezone'])).date()
            else:
                return False
            if target != _nearest_year(target, anchor):
                return False
        return True
    except (ValueError, KeyError, TypeError, OverflowError):
        return False  # Unverifiable reasons remain visible; shared validation still runs.


def _compile(request_id, record, draft, cov):
    sources = {s['input_id']: s for s in [record] + record['context']}
    prefix = hashlib.sha256((request_id + '\0' + record['input_id']).encode('utf-8')).hexdigest()[:24]
    tasks = []
    for index, original in enumerate(draft['tasks'], 1):
        task = copy.deepcopy(original)
        task.update(candidate_id=f'media:{prefix}:{index}', input_id=record['input_id'])
        evidence_ids = {e['input_id'] for e in task['evidence']}
        if not evidence_ids <= sources.keys():
            raise ValueError('foreign_evidence')
        task['sources'] = [dict(input_id=s['input_id'], platform=s['platform'], url=s['source_url'],
                                label=s['source_label'], source_item_id=s.get('source_item_id'))
                           for sid, s in sources.items() if sid in evidence_ids]
        routine_year = (any(r['code'] == 'year_completion' for r in task['review_reasons'])
                        and _routine_year_completion(task, record, cov))
        reasons = [r['message'] for r in task['review_reasons']
                   if not (routine_year and r['code'] == 'year_completion')]
        if any(e['field'] == 'attachment_file' for e in task['evidence']):
            reasons.append('图片引文由模型读取，尚未经原文逐字核对，请确认。')
        if not cov['complete'] or cov['uncertain']:
            reasons.append('部分内容缺失或可读性不确定，请核对原件。')
            if task['deadline']['status'] == 'not_provided':
                task['deadline'].update(status='unknown', basis='未完整可靠读取，截止日期待核实。')
        if task['notice_type'] != 'create':
            reasons.append('请核对通知性质及对应的原任务。')
        if task['course_basis'] == 'inferred':
            reasons.append('课程归属来自内容推断，请核对。')
        if task['deadline']['status'] in ('unknown', 'conflict', 'inferred'):
            reasons.append('截止日期存在疑点，请查看对应证据。')
        task['review_reasons'] = list(dict.fromkeys(reasons))
        task['needs_review'] = bool(task['review_reasons'])
        tasks.append(task)
    return tasks


def _finish(record, summaries, tasks, errors):
    cov = coverage(record, summaries)
    if any(e['stage'] == 'recognize' for e in errors):
        cov['complete'] = False
    status = ('partial_success' if tasks else 'failed') if errors else ('success' if tasks else 'no_tasks')
    return dict(input_id=record['input_id'], status=status, task_ids=[t['candidate_id'] for t in tasks],
                warnings=['部分读取或识别失败，请核对剩余内容。'] if errors else [], coverage=cov, attachments=summaries)


def _record(request_id, record, loader, client, settings, limits, expires):
    summaries, errors, pictures = _prepare(record, loader, limits, expires)
    tasks = []
    prepared_ids = {pid for pid, _ in pictures}
    # Failed files must not suppress an otherwise readable message or attachment.
    readable = pictures or record['text'].strip() or any(c['text'].strip() for c in record['context']) or any(
        a.get('blocks') for a in record['attachments'])
    if readable:
        model_completed = False
        read_retryable = False
        try:
            remaining = expires - time.monotonic()
            if remaining <= 0:
                raise AgentError('agent_timeout', True)
            parts = _parts(record, summaries, pictures, errors)
            instructions = _instructions()
            if len(json.dumps(parts, ensure_ascii=False).encode('utf-8')) + len(instructions.encode('utf-8')) > limits.model_input_bytes:
                raise AgentError('limit_exceeded', False)
            attempt = replace(settings, timeout_seconds=min(settings.timeout_seconds, remaining))
            raw = bounded(_model_call, (client, instructions, parts, attempt), attempt.timeout_seconds)
            model_completed = True
            # Complete means all pixels were provided to a normally finished model call.
            # It never means its quotations or visual interpretation are independently verified.
            for summary in summaries:
                if summary['asset_id'] in prepared_ids:
                    _mark_complete(summary)
            draft = parse_draft(raw, prepared_ids)
            qualities = {q['asset_id']: q for q in draft['image_quality']}
            for summary in summaries:
                if summary['asset_id'] in qualities:
                    summary.update({key: qualities[summary['asset_id']][key] for key in ('quality', 'quality_notes')})
            cov = coverage(record, summaries)
            if not cov['complete'] and not errors:
                errors.append(_error(record, 'incomplete_content', 'recognize'))
            if not draft['tasks'] and cov['uncertain']:
                errors.append(_error(record, 'low_content_quality', 'recognize'))
            tasks = _compile(request_id, record, draft, cov)
            single = dict(contract_version='0.3.1', request_id=request_id, records=[record])
            candidate_result = dict(contract_version='0.3.1', request_id=request_id, tasks=tasks, errors=errors,
                                    record_results=[_finish(record, summaries, tasks, errors)])
            candidate_result['status'] = candidate_result['record_results'][0]['status']
            check_pair(single, candidate_result)
        except (ValueError, ValidationError, KeyError, TypeError):
            tasks = []
            errors.append(_error(record, 'invalid_agent_output', 'recognize'))
        except AgentError as error:
            tasks = []
            read_retryable = error.retryable is True
            code = error.code if error.code in ('agent_timeout', 'agent_unavailable', 'invalid_agent_output', 'limit_exceeded') else 'agent_unavailable'
            errors.append(_error(record, code, 'recognize', retryable=error.retryable))
        finally:
            if not model_completed:
                for summary in summaries:
                    if summary['asset_id'] in prepared_ids:
                        errors.append(_error(record, 'file_read_failed', 'read', summary['asset_id'], read_retryable))
    elif not errors:
        errors.append(_error(record, 'incomplete_content', 'recognize'))
    return _finish(record, summaries, tasks, errors), tasks, errors


def process_media_request(request, asset_loader: AssetLoader, client, settings=None, limits=None):
    """Recognize v0.3.1 without opening an HTTP route or writing application tasks.

    The consumer must enable uploads, authenticate and bind asset_loader to the
    current account; this function never interprets request data as credentials.
    No automatic retries/caching: job-level idempotency belongs to #96.
    """
    try:
        check_request(request)
    except (ValueError, ValidationError, KeyError, TypeError, RecursionError):
        raise MediaInputError('invalid_request') from None
    potential_errors = 3 * len(request['records']) + sum(
        len(a['errors']) + (2 if a.get('content_mode') == 'file' and a['status'] == 'available' else 0)
        for r in request['records'] for a in r['attachments'])
    if potential_errors > 200:
        raise MediaInputError('limit_exceeded')  # Leave room for runtime errors; never truncate them.
    settings = settings or Settings(timeout_seconds=45)
    if settings.max_retries:
        raise ValueError('media_retries_owned_by_caller')
    limits = limits or MediaLimits()
    request = copy.deepcopy(request)
    result = dict(contract_version='0.3.1', request_id=request['request_id'], status='failed',
                  record_results=[], tasks=[], errors=[])
    expires = time.monotonic() + limits.batch_seconds
    for record in request['records']:
        outcome, tasks, errors = _record(request['request_id'], record, asset_loader, client, settings, limits,
                                         min(expires, time.monotonic() + limits.record_seconds))
        combined = dict(result, record_results=result['record_results'] + [outcome],
                        tasks=result['tasks'] + tasks, errors=result['errors'] + errors)
        if len(json_bytes(combined)) > RESULT_MAX_BYTES:
            # Retain earlier successful records; never truncate a candidate or evidence.
            tasks = []
            errors.append(_error(record, 'limit_exceeded', 'recognize'))
            outcome = _finish(record, outcome['attachments'], tasks, errors)
        result['record_results'].append(outcome)
        result['tasks'].extend(tasks)
        result['errors'].extend(errors)
    statuses = [r['status'] for r in result['record_results']]
    result['status'] = ('failed' if all(s == 'failed' for s in statuses) else 'partial_success') if 'failed' in statuses else (
        'partial_success' if 'partial_success' in statuses else 'success' if result['tasks'] else 'no_tasks')
    if len(json_bytes(result)) > RESULT_MAX_BYTES:
        raise MediaInputError('limit_exceeded')  # Even the error/coverage envelope needs a smaller batch.
    check_pair(request, result)
    return result
