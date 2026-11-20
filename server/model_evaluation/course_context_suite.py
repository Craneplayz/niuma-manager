"""Build and score the eight synthetic course-context cases used in PR #213."""
import argparse
import json
from pathlib import Path

from .runner import digest, write_json


NAMES = (
    'two-files-exception', 'conflicting-rules', 'teaching-week',
    'lecture-with-other-assignment', 'primary-short-many-labs',
    'primary-full-many-labs', 'primary-preparation-eight', 'primary-preparation-twelve',
)


def build(out):
    fixture = Path(__file__).resolve().parents[2] / 'contracts/examples/course-context-v01.json'
    source = {case['name']: case for case in json.loads(fixture.read_text())['cases']}
    cases = [
        dict(id='CTX-' + name, family_id='CTX-' + name, module='recognition',
             input_contract='0.4', origin='synthetic', shareable=True,
             scenario='course_context', purpose=name, request=source[name]['request'],
             expected=dict(source[name]['expected'], course_context=True))
        for name in NAMES
    ]
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'cases.json', cases)
    write_json(out / 'vision-cases.json', [])
    return cases


def refinement_status(result, recorded=None):
    """Recover what historical output proves, without guessing from call counts."""
    if recorded in ('completed', 'fallback', 'skipped'):
        return recorded
    if result.get('status') not in ('success', 'no_tasks'):
        return 'unknown'
    decisions = [d for row in result.get('record_results', []) for d in row.get('decisions', [])]
    if any('当前来源已识别；课程参考资料未能完成核对，请确认日期和适用规则。' in d.get('review_reasons', []) for d in decisions):
        return 'fallback'
    if not any(d['recommendation'] != 'filtered' for d in decisions):
        return 'skipped'
    return 'unknown'


def score(expected, result, refinement=None):
    """Count case outcomes; a rejected result is unevaluable, not safe abstention."""
    counts = dict(correct=0, wrong=0, missed=0, safe=0, failed=0, fallback=0, unverified=0)
    records = result.get('record_results', [])
    if result.get('status') not in ('success', 'no_tasks', 'partial_success') or len(records) != 1:
        counts['failed'] = 1
        return counts
    stage = refinement_status(result, refinement)
    if stage == 'fallback':
        counts['fallback'] = 1
        return counts
    decisions = [d for d in records[0].get('decisions', []) if d['recommendation'] != 'filtered']
    deadlines = [d['pair']['assessment']['deadline'] for d in decisions]
    values = [d.get('value') for d in deadlines]
    positive = bool(expected.get('context_date') or expected.get('calendar'))
    routes = [d['recommendation'] for d in decisions]
    if len(decisions) > expected['count']:
        outcome = 'wrong'
    elif positive and not any(values):
        outcome = 'missed'
    elif positive:
        rule = decisions[0].get('calendar_rule') or {}
        evidence = rule.get('evidence', []) if expected.get('calendar') else deadlines[0].get('evidence', [])
        sourced = any(q.get('part_id', '').startswith('context:') for q in evidence)
        calendar_valid = not expected.get('calendar') or deadlines[0].get('derivation') == 'course_rule'
        outcome = 'correct' if values == expected['dates'] and routes == expected['routes'] and sourced and calendar_valid else 'wrong'
    elif any(values) or any(d.get('calendar_rule') for d in decisions):
        outcome = 'wrong'
    elif len(decisions) < expected['count']:
        outcome = 'missed'
    elif routes != expected['routes'] or ('types' in expected and [d.get('content_type') for d in decisions] != expected['types']):
        outcome = 'wrong'
    else:
        outcome = 'safe' if stage in ('completed', 'skipped') else 'unverified'
    counts[outcome] = 1
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    cases = build(parser.parse_args().out)
    print(json.dumps(dict(cases=len(cases), dataset_hash=digest(cases))))
