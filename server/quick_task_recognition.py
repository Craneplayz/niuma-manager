"""Refine one already-created quick task with the configured text recognizer."""
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

import recognition
from text_contract import validate_request, validate_result


def _quick_request(source, baseline):
    zone = ZoneInfo(source['timezone'])
    stamp = source.get('createdAt') or baseline.get('createdAt')
    reference_day = source.get('referenceDay')
    # Resolve relative wording at Add, even if the worker runs tomorrow. When
    # only the preview day is known, never invent a time for “in two hours”.
    if stamp:
        local = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if local.utcoffset() is None:
            raise ValueError('缺少输入时区')
        local = local.astimezone(zone)
        publication = dict(value=local.isoformat(), precision='datetime', basis='source')
    if reference_day and (not stamp or local.date().isoformat() != reference_day):
        publication = dict(value=reference_day, precision='date', basis='source')
    elif not stamp:
        raise ValueError('缺少快捷输入日期')

    request_id = 'quick:' + secrets.token_hex(16)
    record = dict(input_id=request_id, text=source['text'], text_quality='complete', platform='manual',
                  published_at=publication, collected_at=stamp, timezone=zone.key, timezone_basis='caller_default',
                  source_url=None, source_label='一句话添加', source_item_id=None,
                  course_or_project=baseline.get('course') or None, context=[], upstream_hints=None)
    request = dict(contract_version='0.2', request_id=request_id, records=[record])
    validate_request(request)
    return request


def _explicit_due(deadline, timezone):
    if deadline['kind'] != 'deadline' or deadline['status'] != 'explicit':
        return None
    if deadline['precision'] == 'date':
        return dict(date=deadline['value'], precision='date')
    stamp = datetime.fromisoformat(deadline['value'].replace('Z', '+00:00')).astimezone(ZoneInfo(timezone))
    if stamp.second or stamp.microsecond:
        return None  # The editable task stores minutes; do not round a real deadline.
    return dict(date=stamp.date().isoformat(), precision='datetime', time=stamp.strftime('%H:%M'))


def recognize_quick_task(source, baseline):
    """Return canonical changed fields; no import batches, candidates or new tasks are saved."""
    request = _quick_request(source, baseline)
    result = validate_result(request, recognition.recognize(request, RuntimeError))
    if result['errors']:
        raise ValueError('快捷任务识别未完成')
    if len(result['tasks']) != 1:
        return {}
    candidate = result['tasks'][0]
    if candidate['notice_type'] != 'create' or candidate['target_reference'] is not None:
        return {}

    course = candidate['course_or_project']
    cited_course = course and len(course) <= 100 and any(
        'course_or_project' in item['supports'] and course in item['quote'] for item in candidate['evidence'])
    # The v0.2 adapter marks even a verbatim course name as inferred. Accept
    # that specific review reason only when the student's own words support it.
    if candidate['needs_review'] and not (
        cited_course and candidate['course_basis'] == 'inferred'
        and candidate['review_reasons'] == ['课程归属来自正文或上下文推断，请核实。']
    ):
        return {}

    patch = {}
    title = candidate['title'].strip()
    if len(title) <= 180 and title != baseline['title']:
        patch['title'] = title
    if cited_course and course != baseline.get('course'):
        patch['course'] = course
    due = _explicit_due(candidate['deadline'], source['timezone'])
    previous_due = baseline.get('due')
    loses_time = due and due['precision'] == 'date' and previous_due and previous_due['precision'] == 'datetime'
    if due and due != previous_due and not loses_time:
        patch['due'] = due
    return patch
