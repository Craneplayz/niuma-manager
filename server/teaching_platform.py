"""Project authenticated platform facts and shared text results onto teaching decisions."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import hashlib
from pathlib import Path
import re

from contracts.teaching_io import canonical, course_content_page
from ddl_text import course_facts
from ddl_text.time_validation import RELATIVE_DAYS


_COMPONENTS = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (Path(__file__), Path(course_facts.__file__))}


def policy(mode):
    info = dict(mode=mode, components=_COMPONENTS)
    return dict(info, version='teaching-policy-v1:' + hashlib.sha256(canonical(info)).hexdigest())


# A reference to the assignment's own attachment stays in the primary reader.
# Only an instruction that points outside this record requests course context.
_REFERENCE_TARGET = (
    r'《[^《》\n]{1,80}》|(?:课程|教学)?(?:大纲|日历|课件)|课程(?:规定|规则|安排)|'
    r'(?:此前|之前|先前|上次|先前发布的|此前发布的)?(?:通知|公告)|'
    r'(?i:\bsyllabus\b|\bcourse (?:calendar|policy|schedule)\b|\bprevious announcement\b)'
)
_COURSE_REFERENCE = re.compile(
    r'(?:参见|详见|请见|见|参考|根据|按照?|参照|依照|以|'
    r'(?i:\bsee\b|\brefer to\b|\baccording to\b))'
    r'[ \t]*(?:本|课程的|本课程的|老师发布的|the )?(?P<target>' + _REFERENCE_TARGET + r')'
    r'(?:中的?|的)?[ \t]*(?P<title>《[^《》\n]{1,80}》)?'
)


FILE_KINDS = {'attachment', 'content_image'}


def page_only(entry):
    # Captures from before page_attachments already have the crawler's stable IDs.
    return 'page_attachments' in entry or entry['record']['input_id'].startswith(('pku:source:', 'pku:gradebook:'))


def use_page_evidence(entry):
    record = entry['record']
    if 'page_attachments' not in entry:
        entry['page_attachments'] = [dict(ref_id=r['ref_id'], label=r['label'], url=None)
                                     for r in record['references'] if r['kind'] in FILE_KINDS]
    record['references'] = [r for r in record['references'] if r['kind'] not in FILE_KINDS | {'course_context'}]
    record.pop('course_context', None)


def course_references(entry):
    text = entry['record']['text']
    own_files = {(ref.get('label') or '').casefold() for ref in entry['record']['references']
                 if ref['kind'] in ('attachment', 'content_image')}
    own_files.update(ref['label'].casefold() for ref in entry.get('page_attachments', []))
    targets = set()
    for match in _COURSE_REFERENCE.finditer(text):
        target = (match['title'] or match['target']).casefold()
        if target.strip('《》') in own_files:
            continue
        # “以课件和实验为主” describes exam scope, without delegating a rule.
        if re.search(r'(?:选读|拓展阅读|延伸阅读|有兴趣的同学)[^。；\n]{0,16}$', text[max(0, match.start()-25):match.start()]):
            continue
        if match.group().startswith('以') and not re.match(r'[^，。；\n]{0,12}为准', text[match.end():]):
            continue
        targets.add(target)
    return targets


def route(entry):
    if entry.get('platform_item') or course_content_page(entry):
        return 'platform'
    return 'teaching'


def precision(value):
    return 'unknown' if value is None else 'datetime' if 'T' in value else 'date'


def deadline(value=None, evidence=(), derivation=None, anchor=None):
    return dict(value=value, precision=precision(value), derivation=derivation or ('absolute' if value else 'not_provided'),
                evidence=list(evidence), anchor=anchor)


def decision(entry, cid, title, description, action, due, *, reasons=(), relation='unknown',
             content_type='task', filtered=None):
    record = entry['record']
    assessment = dict(input_id=record['input_id'], candidate_id=cid,
                      action=dict(state='supported', evidence=action), deadline=due,
                      unread_relations=[], other_review_reasons=[])
    return dict(title=title, description=description, content_type=content_type, notice_type='create', target=None,
                relevance=dict(state='current', reason='来源条目明确记录此事项。', evidence=action),
                pair=dict(contract_version='0.4-draft.1', record=record, assessment=assessment),
                recommendation='filtered' if filtered else 'candidate' if reasons else 'task',
                review_codes=['model_review'] if reasons else [], review_reasons=list(reasons),
                submission_relation=relation, filter_reason=filtered)


def result(request, decisions):
    manifest = policy('platform-v1')
    entry, = request['records']
    record = entry['record']
    status = 'success' if decisions else 'no_tasks'
    row = dict(input_id=record['input_id'], snapshot_id=record['snapshot_id'], source=entry['source'],
               attempted=True, status=status, decisions=decisions, warnings=[], error=None)
    if not decisions:
        row['empty_decision'] = dict(status=status, reason='教学内容页未发现明确的事项。', evidence=source_evidence(entry))
    return dict(contract_version='0.4', request_id=request['request_id'], classification_mode='platform-v1',
                policy_version=manifest['version'], status=status, record_results=[row]), manifest


def assignment_description(detail):
    """Merge an unambiguous overlap between the crawler's two teacher-text blocks."""
    marker = '\n\n作业详情：'
    if not detail.startswith('列表正文：') or detail.count(marker) != 1:
        return detail
    listing, _, description = detail.removeprefix('列表正文：').partition(marker)
    if not listing or not description:
        return detail

    # Match whole lines in their original order. An attachment list can precede
    # the shared instructions and platform metadata can follow them on either page.
    listing_lines, detail_lines = listing.split('\n'), description.split('\n')
    for first, second in ((listing_lines, detail_lines), (detail_lines, listing_lines)):
        if not second[0] or first.count(second[0]) != 1:
            continue
        start = first.index(second[0])
        overlap = min(len(first) - start, len(second))
        if first[start:start + overlap] == second[:overlap]:
            return '\n'.join(first + second[overlap:])
    return detail


def assignment(request):
    entry, = request['records']
    r, item = entry['record'], entry['platform_item']
    label = entry['source']['label']
    evidence = source_evidence(entry)
    due = (deadline(item['due'], [dict(part_id='source_metadata', quote=item['due'])])
           if item['due'] else body_deadline(entry))
    title = re.sub(r'第\s*[零〇一二两三四五六七八九十百0-9]+\s*[章讲周次]', lambda m: course_facts.sequence_display(m[0]), label)
    detail = r['text'].removeprefix(label).strip()
    reasons = []
    relation = 'unknown'
    if item['kind'] == 'gradebook_assignment':
        relation = 'assignment_submission'
        if not item['submitted']:
            reasons.append('成绩明细列出了这项作业，尚未核实对应的提交页面和状态。')
    elif re.search(r'无[需须]提交|不用提交|不要求提交|no submission', detail, re.I):
        relation = 'other'
    elif item['kind'] == 'native_assignment' or re.search(r'提交|上交|上传|交到|submit|upload', detail, re.I):
        relation = 'assignment_submission'
    if detail and (relation != 'unknown' or item['submitted']):
        evidence.append(dict(part_id='body', quote=detail[:2048]))
    filtered = '成绩明细已明确标为已提交。' if item['submitted'] else None
    description = assignment_description(detail) if item['kind'] == 'native_assignment' else detail
    d = decision(entry, 'platform-assignment', title, description[:4096], evidence,
                 due, reasons=reasons, relation=relation, filtered=filtered)
    return result(request, [d])


def source_evidence(entry):
    record = entry['record']
    text = record['text'].strip()
    label = entry['source']['label']
    if text:
        return [dict(part_id='body', quote=(label if label and label in text else text)[:160])]
    return [dict(part_id='source_metadata', quote=record['source_metadata_text'].strip()[:160])]


# Bind dates to an explicit deadline, an event start, or its revised date.
# Lecture and publication dates on the same page are not submission deadlines.
_MONTH_NUMBERS = {name: i for i, name in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split(), 1)}
_MONTH = r'Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?'
_DATE = re.compile(
    r'(?<![\d./-])(?:(?:(?P<year>20\d\d)\s*[年./-]\s*)?(?P<month>\d{1,2})\s*[月./-]\s*'
    r'(?P<day>\d{1,2})(?!\d)(?:\s*[日号])?|'
    rf'\b(?P<month_name>{_MONTH})\.?\s+(?P<en_day>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<en_year>20\d{{2}}))?)'
    r'(?:[ \t]*[（(](?:周|星期)[一二三四五六日天][）)])?'
    r'(?:(?:[ \t,，]*(?:at|from)\s+|[ T\t]*)(?P<period>上午|下午|晚上|中午|凌晨)?'
    r'(?P<hour>\d{1,2})(?:[：:](?P<minute>\d{2})|[点时](?:(?P<cnminute>\d{1,2})分)?)'
    r'(?:\s*(?P<ampm>am|pm)\b)?)?', re.I)
_BEFORE_DATE = re.compile(r'(?:截止(?:日期|时间)?|到期(?:日期|时间)?|提交(?:截止)?时间|最晚(?:提交)?时间|'
                          r'\bdue(?:\s+(?:date|by|on))?|\bdeadline|\b(?:submit|upload)\s+by)\s*[:：为是]?\s*$', re.I)
_AFTER_DATE = re.compile(r'^\s*(?:截止|(?:之|以)?前\s*(?:截止|提交|交|上传|完成|上交|发)|\bis\s+the\s+deadline)', re.I)
_UNCERTAIN = re.compile(r'待定|待通知|暂定|延期|改期|推迟|取消|原截止|更正|例如|举例|\b(?:tentative|cancelled|canceled|postponed|rescheduled|example|original deadline)\b', re.I)
_MOVE_TO = re.compile(r'(?:改(?:期)?(?:至|到|为)|调整(?:至|到|为)|延(?:期|后)(?:至|到)|推迟(?:至|到)|提前(?:至|到)|'
                      r'\b(?:moved|rescheduled|postponed|changed)\s+(?:to|until)|\bnew\s+(?:date|time))'
                      r'\s*[:：]?\s*$', re.I)
_CANCEL = re.compile(r'取消|不再举行|不再举办|待定|另行通知|暂定|\b(?:cancelled|canceled|tentative|tbd|to be determined)\b', re.I)
_EVENT = re.compile(r'考试|测验|测试|答辩|讲座|讲演|研讨会|宣讲会|报告会|上课|说明会|'
                    r'\b(?:exam(?:ination)?|midterm|final|quiz|test|lecture|seminar|meeting|session|class|presentation)\b', re.I)
_EVENT_AT = re.compile(r'(?:将(?:在|于)|定于|安排在|举行时间|开始时间|时间[为是：:]|日期[为是：:]|'
                       r'\b(?:held|scheduled|takes? place|starts?|begins?|is|will be)\s+(?:on\s+)?|\b(?:exam|midterm|lecture|seminar|meeting)\s+on)\s*$', re.I)
_CLOCK_RANGE = re.compile(r'^[ \t]*[-~～–—至][ \t]*\d{1,2}[：:]\d{2}(?![\d:])')
_UNPARSED_TIME = re.compile(r'^\s*(?:[:：]|\d|[上下]午|晚上|中午|凌晨|至|到|[-~～–—]|[ap]\.?m\b|[+-]\d|Z\b|(?:at|from)\s+\d)', re.I)
_RELATIVE_CLOCK = re.compile(
    r'(?P<day_word>' + '|'.join(sorted(RELATIVE_DAYS, key=len, reverse=True)) + r')[ \t]*'
    r'(?P<period>上午|下午|晚上|早上|凌晨|中午)?[ \t]*(?P<hour>\d{1,2})'
    r'(?:[：:](?P<minute>\d{2})|[点时](?:(?P<cnminute>\d{1,2})分)?)(?![\d半刻分])')
_EVENT_NOUN = r'小班课|大班课|课程|考试|测验|测试|答辩|讲座|研讨会|报告会|说明会'
_EVENT_AFTER_CLOCK = re.compile(
    rf'^\s*(?:(?:为|是|有|召开|举行|举办|开)[^，,。；;！？!?\n]{{0,24}}?(?:{_EVENT_NOUN})'
    r'(?=[，,。；;！？!?\s]|$)|上课|开课)')


def publication_day(record):
    publication = record.get('published_at')
    if not publication or publication['basis'] != 'source':
        return None
    value = publication['value']
    if len(value) == 10:
        return date.fromisoformat(value)
    instant = datetime.fromisoformat(value.replace('Z', '+00:00').replace('z', '+00:00'))
    return instant.astimezone(ZoneInfo(record['timezone'])).date() if instant.tzinfo else None


def anchored_day(record, month, day, year, *, notice):
    if year:
        return date(int(year), month, day), None
    term = record.get('course_term')
    published = publication_day(record)
    if term and (term['applies_to'] == 'current_assignment' or notice):
        start, end = date.fromisoformat(term['starts_on']), date.fromisoformat(term['ends_on'])
        if not published or start <= published <= end:
            for year in range(start.year, end.year + 1):
                if (start.year, start.month, start.day) <= (year, month, day) <= (end.year, end.month, end.day):
                    return date(year, month, day), 'course_term'
    if published:
        choices = []
        for year in (published.year-1, published.year, published.year+1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            choices.append((abs((candidate-published).days), candidate))
        choices.sort()
        if choices and choices[0][0] <= 180:
            return choices[0][1], 'published_at'
    raise ValueError('date has no captured year anchor')


def normalized_date(record, match, *, notice):
    month = (_MONTH_NUMBERS[match['month_name'][:3].lower()]
             if match['month_name'] else int(match['month']))
    day, anchor = anchored_day(record, month, int(match['day'] or match['en_day']),
                               match['year'] or match['en_year'], notice=notice)
    value = day.isoformat()
    if match['hour'] is not None:
        hour = int(match['hour'])
        meridiem = match['period'] or (match['ampm'] or '').lower()
        if meridiem:
            if not 1 <= hour <= 12:
                raise ValueError('invalid half-day clock')
            hour = hour % 12 + (12 if meridiem in ('pm','下午','晚上','中午') else 0)
        clock = time(hour, int(match['minute'] or match['cnminute'] or 0))
        value = datetime.combine(day, clock, ZoneInfo(record['timezone'])).isoformat()
    return deadline(value, [dict(part_id='body', quote=match[0])], 'missing_year' if anchor else 'absolute', anchor)


def relative_event_time(record, match):
    published = publication_day(record)
    if published is None:
        raise ValueError('relative event needs a publication date')
    word, period = match['day_word'], match['period']
    implicit = {'早': '早上', '晚': '晚上'}.get(word[-1])
    if implicit and period and implicit != period:
        raise ValueError('conflicting periods')
    period = implicit or period
    hour = int(match['hour'])
    if period == '晚上':
        if 5 <= hour <= 11:
            hour += 12
        if not 17 <= hour <= 23:
            raise ValueError('ambiguous evening clock')
    elif period in ('下午', '中午'):
        if 1 <= hour <= 11:
            hour += 12
        if hour < 12 or (period == '中午' and hour > 13):
            raise ValueError('ambiguous afternoon clock')
    elif period and hour >= 12:
        raise ValueError('ambiguous morning clock')
    day = published + timedelta(days=RELATIVE_DAYS[word])
    clock = time(hour, int(match['minute'] or match['cnminute'] or 0))
    local = datetime.combine(day, clock, ZoneInfo(record['timezone']))
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise ValueError('ambiguous local clock')
    return deadline(local.isoformat(), [dict(part_id='body', quote=match[0])], 'relative', 'published_at')


def body_deadline(entry, *, notice=False):
    record, text = entry['record'], entry['record']['text']
    if (_CANCEL if notice else _UNCERTAIN).search(text):
        return deadline()
    regular, revised = {}, {}
    dates = list(_DATE.finditer(text))
    if notice:
        dates.extend(_RELATIVE_CLOCK.finditer(text))
    for match in dates:
        before = text[max(0, match.start() - 90):match.start()]
        after = text[match.end():match.end() + 48]
        # Ignore the superseded date when the teacher explicitly contrasts it with a new one.
        clause = re.split(r'[。；;，,\n]', before)[-1]
        changed = notice and _MOVE_TO.search(before)
        if notice and not changed and re.search(r'原(?:定|来|先|截止)|\b(?:originally|previously|former)\b', clause, re.I):
            continue
        event = (notice and _EVENT_AT.search(before) and _EVENT.search((entry['source']['label'] or '') + ' ' + clause)
                 and not re.search(r'发布|上传|资料|课件|\b(?:posted|uploaded|published|materials)\b', clause[-24:], re.I)
                 and not re.match(r'^\s*(?:发布|上传)', after))
        relative = match.re is _RELATIVE_CLOCK
        if relative:
            # A teacher can put the time before the class name, after an optional end clock.
            span = _CLOCK_RANGE.match(after)
            tail = after[span.end():] if span else after
            following = _EVENT_AFTER_CLOCK.match(tail)
            event = (event or following and not re.search(r'报名|复习|备考|准备|资料|课件|发布|上传', following[0])
                     or _EVENT.search(clause) and re.match(r'^\s*(?:举行|召开|开始|开讲|上课)(?=[，,。；;！？!?\s]|$)', tail))
            context = (entry['source']['label'] or '') + ' ' + re.split(r'[。；;\n]', before)[-1]
            if (not event and not (changed and _EVENT.search(context))
                    or re.search(r'(?:结束|散场|截止)(?:时间)?[为是：:]?\s*$', before)
                    or re.match(r'^\s*(?:结束|散场|发布|上传|截止)', tail)):
                continue
        if not (changed or event or _BEFORE_DATE.search(before) or _AFTER_DATE.search(after)):
            continue
        # A time range attached to an event supplies its start. A malformed time,
        # or a deadline range, cannot safely be reduced to its first endpoint.
        if _UNPARSED_TIME.match(after) and not ((event or changed) and match['hour'] and _CLOCK_RANGE.match(after)):
            return deadline()
        try:
            day = relative_event_time(record, match) if relative else normalized_date(record, match, notice=notice)
        except (ValueError, OverflowError):
            return deadline()
        values = revised if changed else regular
        values[day['value']] = day
    matches = revised or regular
    if revised and set(regular) - set(revised):
        return deadline()
    if not revised and notice and _UNCERTAIN.search(text):
        return deadline()
    return next(iter(matches.values())) if len(matches) == 1 else deadline()


# Course-content lists mix lecture files with actionable posts. A bare date
# (often the lecture date) or a keyword inside a filename isn't an action.
_TASK_TITLE = re.compile(r'作业|必读|必做|必交|\b(?:homework|assignment|problem set|required reading)\b', re.I)
_ACTION = re.compile(r'(?:务必|必须|须|需要|要求|请)(?:大家|同学们?|各位同学)?[^。；;\n]{0,18}'
                     r'(?:提交|上交|交回|上传|填写|报名|选题|完成|预习|阅读)|(?:读完|阅读|提交|上交)[^。；;\n]{0,16}(?:必读|必做)|'
                     r'\b(?:please|must|required to)\s+(?:submit|upload|complete|register|read|fill out|sign up)\b|'
                     r'\b(?:submit|turn in|upload)\s+(?:by|before)\b', re.I)
_NOTICE = re.compile(r'考试|测验|闭卷|开卷|答辩|调课|停课|补课|换教室|上课地点|'
                     r'\b(?:exam(?:ination)?s?|midterms?|finals?|quizzes|quiz|class cancel(?:l?ed|lation)|room change|rescheduled)\b', re.I)
_SCHEDULE = re.compile(r'安排|说明|时间|地点|教室|须知|要求|调整|规则|将于|占比|\d{1,2}月\d|\d{1,2}:\d{2}|'
                       r'\b(?:schedule|rules|instructions|held|room|postponed|cancelled|on (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\b', re.I)
_REFERENCE_TITLE = re.compile(r'参考答案|解题|样题|真题|样例|示例|往年|模拟题|复习资料|'
                              r'\b(?:sample|practice|past|mock)\s+(?:exam|quiz|assignment|paper)|\b(?:solutions?|answer key)\b', re.I)


_OPTIONAL = re.compile(
    r'不(?:作|做|作为|算作|属于|在|列入|计入|纳入|涉及|包括|包含|要求|需要|必|用|考)'
    r'|无[需须]|不[是作].{0,6}(?:重点|考点)|可选|选读|仅供(?:参考|复习)|'
    r"\b(?:optional|not examinable|not (?:part of|on|in|required for) (?:the )?(?:exam|test)"
    r"|won.t be (?:tested|examined)|(?:not|do not|don.t) (?:need to |have to )?(?:submit|upload|read))\b", re.I)
_CHANGE = re.compile(r'(?:考试|测验|答辩|课程|上课).{0,8}(?:取消|推迟|延期|改期|改至|调整至|暂停)|'
                     r'(?:取消|暂停|不再举行).{0,8}(?:考试|测验|答辩|课程)|'
                     r'\b(?:cancelled|canceled|rescheduled|postponed)\b', re.I)


def course_content_type(label, text):
    # An exam mentioned in one lecture paragraph and a requirement in another
    # do not establish an exam announcement. Check the same teacher statement.
    body = text.removeprefix(label).strip()
    statements = [s for s in re.split(r'[。；;！？!?\n]|(?<=\.)\s+', body) if s.strip()]
    actionable = [s for s in statements if not _OPTIONAL.search(s)]
    if _REFERENCE_TITLE.search(label):
        return 'task' if any(_ACTION.search(s) for s in actionable) else None
    if _NOTICE.search(label) and (not _OPTIONAL.search(label) or _CHANGE.search(label)):
        return 'notice'
    if any(_NOTICE.search(s) and (_SCHEDULE.search(s) or _CHANGE.search(s))
           for s in statements if not _OPTIONAL.search(s) or _CHANGE.search(s)):
        return 'notice'
    if (_TASK_TITLE.search(label) and not _OPTIONAL.search(label)) or any(_ACTION.search(s) for s in actionable):
        return 'task'
    return None


def classify(request):
    entry, = request['records']
    item = entry.get('platform_item')
    if item and item['kind'] != 'announcement':
        return assignment(request)
    record = entry['record']
    label = entry['source']['label'] or record['text'].strip().split('\n', 1)[0][:512]
    kind = 'notice' if item else course_content_type(label, record['text'])
    if not kind:
        return result(request, [])
    candidate_id = 'platform-announcement' if item else 'course-content-item'
    description = record['text'].removeprefix(label).strip()
    return result(request, [decision(entry, candidate_id, label, description[:4096], source_evidence(entry),
                                     body_deadline(entry, notice=kind == 'notice'), content_type=kind)])
