"""Project authenticated platform facts and shared text results onto teaching decisions."""
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
from pathlib import Path
import re

from contracts.teaching_io import canonical
from ddl_text import course_facts


_COMPONENTS = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (Path(__file__), Path(course_facts.__file__))}


def policy(mode, text_policy=None):
    info = dict(mode=mode, components=_COMPONENTS, text_policy=text_policy)
    return dict(info, version='teaching-policy-v1:' + hashlib.sha256(canonical(info)).hexdigest())


def route(entry):
    item = entry.get('platform_item')
    if not item:
        return 'teaching'
    r = entry['record']
    if item['kind'] == 'gradebook_assignment':
        return 'platform'
    text = r['text']
    # Preserve the teaching reader for multi-part instructions and facts which
    # the source explicitly delegates to an attachment or another announcement.
    if r['references'] or r['body_status'] != 'complete':
        return 'teaching'
    if re.search(r'(?:详见|参见|见|按照?|根据).{0,12}(?:附件|大纲|课件|此前通知|课程日历)', text):
        return 'teaching'
    if item['kind'] == 'announcement':
        if not r['published_at'] or r['published_at']['basis'] != 'source':
            return 'teaching'
        return 'text'
    if len(text) > 4096 or re.search(r'分阶段|分批提交|分别提交|另行提交|另交|另需|还需|还要|同时还|此外|in addition|also submit|延期|改期|取消|推迟|提前至|调整到|调整至', text, re.I):
        return 'teaching'
    if not item['due'] and re.search(r'截止|之前|前提交|deadline|due by', text, re.I):
        return 'teaching'
    return 'platform'


def precision(value):
    return 'unknown' if value is None else 'datetime' if 'T' in value else 'date'


def deadline(value=None, evidence=(), derivation=None, anchor=None):
    return dict(value=value, precision=precision(value), derivation=derivation or ('absolute' if value else 'not_provided'),
                evidence=list(evidence), anchor=anchor)


def decision(entry, cid, title, description, action, due, *, reasons=(), notice='create', target=None,
             relation='unknown', content_type='task', filtered=None):
    record = entry['record']
    assessment = dict(input_id=record['input_id'], candidate_id=cid,
                      action=dict(state='supported', evidence=action), deadline=due,
                      unread_relations=[], other_review_reasons=[])
    return dict(title=title, description=description, content_type=content_type, notice_type=notice, target=target,
                relevance=dict(state='current', reason='来源条目明确记录此事项。', evidence=action),
                pair=dict(contract_version='0.4-draft.1', record=record, assessment=assessment),
                recommendation='filtered' if filtered else 'candidate' if reasons else 'task',
                review_codes=['model_review'] if reasons else [], review_reasons=list(reasons),
                submission_relation=relation, filter_reason=filtered)


def result(request, mode, version, decisions):
    entry, = request['records']
    record = entry['record']
    status = 'success' if decisions else 'no_tasks'
    row = dict(input_id=record['input_id'], snapshot_id=record['snapshot_id'], source=entry['source'],
               attempted=True, status=status, decisions=decisions, warnings=[], error=None)
    if not decisions:
        row['empty_decision'] = dict(status=status, reason='通知识别未发现待办。', evidence=[
            dict(part_id='body', quote=record['text'][:160])])
    return dict(contract_version='0.4', request_id=request['request_id'], classification_mode=mode,
                policy_version=version, status=status, record_results=[row])


def assignment(request):
    entry, = request['records']
    r, item = entry['record'], entry['platform_item']
    label = entry['source']['label']
    evidence = [dict(part_id='body', quote=label[:160])]
    due = item['due']
    date_evidence = [dict(part_id='source_metadata', quote=due)] if due else []
    title = re.sub(r'第\s*[零〇一二两三四五六七八九十百0-9]+\s*[章讲周次]', lambda m: course_facts.sequence_display(m[0]), label)
    detail = r['text'].removeprefix(label).strip()
    reasons = []
    relation = 'unknown'
    if item['kind'] == 'gradebook_assignment':
        if not item['submitted']:
            reasons.append('成绩明细列出了这项作业，尚未核实对应的提交页面和状态。')
    elif re.search(r'无[需须]提交|不用提交|不要求提交|no submission', detail, re.I):
        relation = 'other'
    elif re.search(r'提交|上交|上传|交到|submit|upload', detail, re.I):
        relation = 'assignment_submission'
    if detail and (relation != 'unknown' or item['submitted']):
        evidence.append(dict(part_id='body', quote=detail[:2048]))
    filtered = '成绩明细已明确标为已提交。' if item['submitted'] else None
    d = decision(entry, r['input_id'] + ':assignment', title, detail[:4096], evidence,
                 deadline(due, date_evidence), reasons=reasons, relation=relation, filtered=filtered)
    manifest = policy('platform-v1')
    return result(request, 'platform-v1', manifest['version'], [d]), manifest


def notice_request(request):
    entry, = request['records']
    r, source = entry['record'], entry['source']
    publication = r['published_at']['value']
    record = dict(input_id=r['input_id'], text=r['text'], text_quality=r['body_status'], platform='teaching_web',
                  published_at=dict(value=publication, precision=precision(publication), basis='source'),
                  collected_at=r['collected_at'], timezone=r['timezone'], timezone_basis='source',
                  source_url=source['url'], source_label=source['label'], source_item_id=r['source_item_id'],
                  course_or_project=source['course_or_project'], context=[], upstream_hints=None)
    return dict(contract_version='0.2', request_id=request['request_id'], records=[record])


def notice_deadline(source_deadline, record):
    if source_deadline['status'] == 'not_provided':
        return deadline()
    raw = source_deadline['raw']
    value = source_deadline['value']
    if (source_deadline['kind'] != 'deadline' or not value or not raw
            or raw not in record['text'] or len(raw) > 2048):
        return None
    evidence = [dict(part_id='body', quote=raw)]
    if source_deadline['precision'] == 'datetime':
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        value = stamp.astimezone(ZoneInfo(record['timezone'])).isoformat()
    if re.search(r'今天|明天|后天|下周|本周|\d+天后', raw):
        return deadline(value, evidence, 'relative', 'published_at')
    if source_deadline['status'] == 'explicit':
        return deadline(value, evidence)
    return None


def notice_result(request, output, text_policy):
    """Return None when v0.2 cannot express the source's time/evidence faithfully."""
    if output['status'] not in ('success', 'no_tasks') or len(output['tasks']) > 50:
        return None
    entry, = request['records']
    text = entry['record']['text']
    items = []
    for task in output['tasks']:
        action = [dict(part_id='body', quote=e['quote']) for e in task['evidence']
                  if 'title' in e['supports'] and e['quote'] in text and 0 < len(e['quote']) <= 2048]
        d, reasons = task['deadline'], list(task['review_reasons'])
        if (not action or len(action) > 20 or len(reasons) > 100 or any(len(reason) > 1024 for reason in reasons)
                or task['prerequisites'] or d['alternatives'] or len(task['candidate_id']) > 256
                or len(task['title']) > 512 or len(task['description']) > 4096):
            return None
        due = notice_deadline(d, entry['record'])
        if due is None:
            return None
        target = task['target_reference']
        if target and (target not in text or len(target) > 2048):
            return None
        if task['notice_type'] != 'create' or task['needs_review']:
            reasons = reasons or ['请核对通知及其对应的任务。']
        items.append(decision(entry, task['candidate_id'], task['title'], task['description'], action, due,
                              notice=task['notice_type'], target=dict(part_id='body',quote=target) if target else None,
                              reasons=reasons, content_type='task' if task['notice_type']=='create' else 'notice'))
    manifest = policy('text-v0.2', text_policy)
    return result(request, 'text-v0.2', manifest['version'], items), manifest
