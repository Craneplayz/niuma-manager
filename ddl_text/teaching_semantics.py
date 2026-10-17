"""Conservative time-role normalization; source dates are not task deadlines."""
import re


TIME_ROLES = ['deadline', 'event', 'assigned', 'published', 'reference', 'none', 'unknown']
SUBMISSION_RELATIONS = ['assignment_submission', 'other', 'unknown']


def normalize_time(draft, record):
    deadline = draft['assessment']['deadline']
    if deadline['value'] is None: return
    role = draft.get('time_role')
    source_date = role in ('assigned', 'published', 'reference', 'none')
    # Narrow fallback for dated homework headings, including older saved drafts.
    # Do not reject same-day deadlines or dates from attached instructions.
    heading = record['text'].splitlines()[0] if record['text'] else ''
    evidence = deadline['evidence']
    body_only = evidence and all(e.get('part_id') == 'body' for e in evidence)
    if (role in (None, 'unknown') and draft.get('content_type', 'task') == 'task' and body_only
            and re.search(r'作业|习题|homework|assignment|exercise', heading, re.I)):
        year, month, day = map(int, deadline['value'][:10].split('-'))
        dated = re.search(rf'(?<!\d)(?:{year}\s*[-/.年]\s*)?0?{month}\s*[-/.月]\s*0?{day}(?:日|号)?(?!\d)', heading)
        binding = re.search(r'截止|到期|最[迟晚]|提交期限|提交时间|交作业时间|交作业日期|due\b|deadline\b|submit\b|hand\s+in\b|[前之]\s*(?:提交|交|完成)|(?:提交|交|完成).*?(?:之前|以前|为止)', record['text'], re.I)
        if dated and not binding: source_date = True
    if source_date:
        deadline.update(value=None, precision='unknown', derivation='not_provided', anchor=None, evidence=[])

