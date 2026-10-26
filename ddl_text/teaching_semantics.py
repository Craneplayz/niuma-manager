"""Conservative time-role normalization; source dates are not task deadlines."""
import re


TIME_ROLES = ['deadline', 'event', 'assigned', 'published', 'reference', 'none', 'unknown']
SUBMISSION_RELATIONS = ['assignment_submission', 'other', 'unknown']


def normalize_time(draft, record):
    """Clear explicit non-deadline roles; flag ambiguous dated headings for review."""
    deadline = draft['assessment']['deadline']
    if deadline['value'] is None: return False
    role = draft.get('time_role')
    if role in ('assigned', 'published', 'reference', 'none'):
        deadline.update(value=None, precision='unknown', derivation='not_provided', anchor=None, evidence=[])
        return False
    # A dated heading cannot establish either a deadline or a publication date.
    # Preserve evidence in old/unknown-role drafts instead of guessing from a
    # finite list of submission words. Explicit roles and attachment dates win.
    lines = record['text'].splitlines()
    # Without a separate body, the first line may be the entire instruction.
    # Do not treat an arbitrary one-line deadline sentence as a source heading.
    heading = lines[0] if len(lines) > 1 else ''
    evidence = deadline['evidence']
    body_only = evidence and all(e.get('part_id') == 'body' for e in evidence)
    if (role in (None, 'unknown') and draft.get('content_type', 'task') == 'task' and body_only
            and re.search(r'作业|习题|homework|assignment|exercise', heading, re.I)):
        year, month, day = map(int, deadline['value'][:10].split('-'))
        dated = re.search(rf'(?<!\d)(?:{year}\s*[-/.年]\s*)?0?{month}\s*[-/.月]\s*0?{day}(?:日|号)?(?!\d)', heading)
        # Positive wording can retain the model's ready classification. Absence
        # only requests review: it never establishes that a deadline is absent.
        binding = re.search(r'截止|到期|最[迟晚]|提交期限|提交时间|交作业时间|交作业日期|due\b|deadline\b|submit\b|hand\s+in\b|[前之]\s*(?:提交|交|完成)|(?:提交|交|完成).*?(?:之前|以前|为止)', record['text'], re.I)
        return bool(dated and not binding)
    return False

