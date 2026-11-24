"""Source-backed presentation and deterministic teaching-week arithmetic.

The model identifies applicable facts; Python renders a common title and does
calendar arithmetic. These facts never authorize merging or completing tasks.
"""
import re
import unicodedata
from datetime import date,timedelta

from contracts.course_facts import KINDS, fact_schemas as schemas


class PrimaryEvidenceError(ValueError):
    """The proposed action belongs only to supporting documents."""


def compact(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',text)).casefold()


def _sequence_number(value):
    """Read only unambiguous ordinary numerals below 1000, not dates/indices."""
    if re.fullmatch(r'[0-9]{1,3}',value): return int(value)
    digits=dict(zip('零〇一二两三四五六七八九',(0,0,1,2,2,3,4,5,6,7,8,9)))
    if value in digits: return digits[value]
    tens=re.fullmatch(r'([一二三四五六七八九])?十([一二三四五六七八九])?',value)
    if tens: return digits.get(tens[1],1)*10+digits.get(tens[2],0)
    hundreds=re.fullmatch(r'([一二两三四五六七八九])百(?:(零[一二三四五六七八九])|([一二三四五六七八九]十[一二三四五六七八九]?))?',value)
    if hundreds:
        remainder=digits[hundreds[2][-1]] if hundreds[2] else _sequence_number(hundreds[3]) if hundreds[3] else 0
        return digits[hundreds[1]]*100+remainder
    # Colloquial 一百二, ranges, decimal indices and digit-by-digit years are
    # intentionally left alone instead of guessing a batch number.
    return None


def sequence_display(value):
    """Canonical display only. Never use this value for identity or merging."""
    normalized=unicodedata.normalize('NFKC',value).strip()
    chinese=re.fullmatch(r'第\s*([0-9零〇一二两三四五六七八九十百]+)\s*([章讲周次])',normalized)
    if chinese:
        number=_sequence_number(chinese[1])
        if number is not None:return f'第{number}{chinese[2]}'
    english=re.fullmatch(r'(chapter|lecture|week|assignment|lab)\s*#?\s*([0-9]{1,3})',normalized,re.IGNORECASE)
    if english:
        unit=dict(chapter='章',lecture='讲',week='周',assignment='次',lab='次')[english[1].lower()]
        return f'第{int(english[2])}{unit}'
    return value


def title(draft):
    facts=draft.get('title_facts')
    if facts is None: return draft['title']  # Saved historical drafts remain compatible.
    source=compact('\n'.join(q['quote'] for q in facts['evidence']))
    for name in ('channel','sequence','subject'):
        value=facts[name].strip()
        if value and compact(value) not in source:
            raise ValueError('title facts must be present in their quoted source')
    head=facts['channel'].strip()+KINDS[facts['kind']]
    # Numbered deliverables keep the same visible template across chapters.
    # Topic/detail remains in description and in the persisted fact object.
    fields=('sequence',) if facts['kind'] in ('assignment','lab') and facts['sequence'].strip() else ('sequence','subject')
    # Validate the original literal facts first, then normalize only their
    # sequence display. Evidence and the persisted title_facts stay unchanged.
    details=list(dict.fromkeys(sequence_display(facts[name].strip()) if name=='sequence' else facts[name].strip()
                               for name in fields if facts[name].strip()))
    if fields==('sequence',) and facts['subject'].strip() and compact(facts['subject']) not in compact(draft['description']):
        suffix='；'+facts['subject'].strip()
        if len(draft['description'])+len(suffix)<=4096:draft['description']+=suffix
    return ' · '.join([head,*details])


def calendar(draft,record):
    rule=draft.get('calendar_rule')
    if rule is None: return None
    deadline=draft['assessment']['deadline']
    quotes='\n'.join(q['quote'] for q in rule['evidence'])
    monday=date.fromisoformat(rule['week_one_monday'])
    year,month,day=monday.year,monday.month,monday.day
    # Semester starts_on is an approximate enrollment window, never week one.
    anchored=bool(re.search(rf'(?<!\d){year}\s*[-/.年]\s*0?{month}\s*[-/.月]\s*0?{day}(?:日|号)?(?!\d)',quotes))
    value=monday+timedelta(weeks=rule['week']-1,days=rule['weekday']-1+rule['offset_days'])
    term=record.get('course_term')
    if (not anchored or monday.weekday()!=0 or not term
            or not date.fromisoformat(term['starts_on'])<=value<=date.fromisoformat(term['ends_on'])):
        deadline.update(value=None,precision='unknown',derivation='unresolved',anchor=None,evidence=rule['evidence'])
        return '课程周次规则缺少可核验的第一周周一，或计算日期超出适用学期，请核对。'
    # Do not invent a time-of-day for a week rule. Exact times stay in original
    # evidence/description; this API returns only the day that was calculated.
    deadline.update(value=value.isoformat(),precision='date',derivation='course_rule',anchor='course_term',evidence=rule['evidence'])
    return '已按课程周次规则计算日期；请确认周次、例外安排和该规则对本条要求的适用性。'


def check_primary_action(draft,record):
    context={d['ref_id'] for d in record.get('course_context',{}).get('documents',[])}
    if not context: return
    evidence=draft['assessment']['action']['evidence']
    if draft['recommendation']=='filtered':
        evidence=evidence+draft['relevance']['evidence']
    if not any(q['part_id'] not in context for q in evidence):
        raise PrimaryEvidenceError('including or filtering an item needs primary-source evidence; course context is only supporting material')


def check_primary_empty(empty,record):
    context={d['ref_id'] for d in record.get('course_context',{}).get('documents',[])}
    if (context and empty['status']=='no_tasks'
            and not any(q['part_id'] not in context for q in empty['evidence'])):
        raise PrimaryEvidenceError('no_tasks needs primary-source evidence; another course document cannot establish an empty current source')
