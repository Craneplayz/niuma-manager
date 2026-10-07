"""Synthetic policy fixtures derived from the single shared draft contract."""
import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_CASES = json.loads((ROOT / 'contracts/examples/teaching-review-v0.4-draft-cases.json').read_text(
    encoding='utf-8'))['cases']


def policy_case(name='unread-supplement'):
    return deepcopy(next(c for c in POLICY_CASES if c['name'] == name))


def model_task(pair):
    assessment = deepcopy(pair['assessment'])
    for key in ('candidate_id', 'input_id'):
        assessment.pop(key)
    return dict(title='完成第二次作业', description='按通知完成练习。', notice_type='create', target=None,
                relevance=dict(state='current', reason='通知中的行动要求。', evidence=[]), assessment=assessment)


def raw_task(pair):
    return json.dumps(dict(tasks=[model_task(pair)]), ensure_ascii=False)


def live_cases():
    names = ['unread-supplement', 'yearless-current-assignment-term', 'yearless-unknown-age',
             'unread-authority', 'cross-year-term', 'old-publication-keeps-old-year']
    cases = []
    for name in names:
        case = policy_case(name)
        codes = case['expected_review_codes']
        deadline = case['pair']['assessment']['deadline']
        # The shared pair tests only date policy, not the full NLP conclusion:
        # its 2023 publication and 2026 "current assignment" metadata conflict.
        route = 'candidate' if codes or name == 'old-publication-keeps-old-year' else 'task'
        cases.append(dict(name=name, record=case['pair']['record'], source_verified=case['source_verified'],
                          expected=dict(recommendation=route, value=deadline['value'])))
    historical = deepcopy(cases[-1])
    historical['name'] = 'old-publication-without-conflict'
    historical['record']['source_metadata_text'] = '2023秋季学期，2023-09-01至2024-01-31；该学期布置的作业。'
    historical['expected']['recommendation'] = 'filtered'
    cases.append(historical)
    case = policy_case('explicit-complete')
    record = case['pair']['record']
    record.update(text='请于2026.9.29 9:00前交一份课程内容回顾，内容范围为目前学过的全部内容（包括当次课），请大家尽快完成，会按比例计入总评。',
                  collected_at='2026-09-25T12:00:00+08:00')
    cases.append(dict(name='explicit-with-urgency', record=record, source_verified=True,
                      expected=dict(recommendation='task', value='2026-09-29T09:00:00+08:00')))
    return cases


def matches(case, decisions):
    if len(decisions) != 1:
        return False
    actual, expected = decisions[0], case['expected']
    return (actual['recommendation'] == expected['recommendation']
            and actual['pair']['assessment']['deadline']['value'] == expected['value']
            and (expected['recommendation'] != 'task' or not actual['review_reasons']))
