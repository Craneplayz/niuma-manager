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
    entry, = value['request']['records']
    record = entry['record']
    item = entry.get('platform_item')
    if item:
        kind = item['kind']
        if kind == 'native_assignment':
            if not assignment or assignment['state'] != 'active':
                raise ValueError('native assignment requires its active detail page')
        elif kind == 'listed_assignment':
            if item['due'] is not None:
                raise ValueError('listed assignment only identifies the source kind')
        elif assignment is not None or 'gradebook_id' in item:
            raise ValueError('unexpected native assignment binding')
        if item['submitted'] and not (kind == 'gradebook_assignment' or kind == 'listed_assignment' and item.get('gradebook_id')):
            raise ValueError('unexpected gradebook submission signal')
        if kind == 'announcement' and item['due'] is not None:
            raise ValueError('unexpected announcement deadline')
        if item['due'] and item['due'] not in record['source_metadata_text']:
            raise ValueError('platform deadline missing from metadata')
        if not entry['source']['label'] or (kind in ('native_assignment', 'gradebook_assignment') and entry['source']['label'] not in record['text']):
            raise ValueError('missing platform label')
    if assignment is not None:
        if (assignment['basis'] != BASES[assignment['state']] or assignment['course_id'] != record['course_id']
                or assignment['content_id'] != record['source_item_id']):
            raise ValueError('assignment identity or basis mismatch')
    return value
