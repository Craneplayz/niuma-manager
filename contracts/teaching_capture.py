"""Internal authenticated crawler envelope; never accepted from browser/model JSON."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from .teaching_io import canonical, check_request, MAX_BYTES

SCHEMA = json.loads((Path(__file__).parent/'schemas/teaching-capture-v0.1.schema.json').read_text(encoding='utf-8'))
BASES = {'submitted': 'review_submission_history_title', 'active': 'active_assignment_page', 'unknown': 'unknown'}


def check_capture(value):
    if len(canonical(value)) > MAX_BYTES: raise ValueError('capture exceeds budget')
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(value)
    check_request(value['request'])
    assignment = value['assignment']
    if assignment is not None:
        record = value['request']['records'][0]['record']
        if (assignment['basis'] != BASES[assignment['state']] or assignment['course_id'] != record['course_id']
                or assignment['content_id'] != record['source_item_id']):
            raise ValueError('assignment identity or basis mismatch')
    return value
