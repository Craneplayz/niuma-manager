"""Inspect explicitly provided actions separately from adapter validity."""
import argparse
import json
from pathlib import Path


def score(report, rubric, cases):
    case_map = {case['id']: case for case in cases}
    rows = []
    summary = []
    for model in sorted({row['model'] for row in report['rows']}):
        selected = []
        for row in report['rows']:
            if row['model'] != model or row['module'] != 'decomposition':
                continue
            draft = {}
            try:
                draft = json.loads(row.get('raw_outputs', [])[-1])
            except (ValueError, IndexError, TypeError):
                pass
            if not isinstance(draft, dict):
                draft = {}
            plan = draft.get('plan')
            steps = plan.get('subtasks', []) if isinstance(plan, dict) else []
            steps = [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
            text = ' '.join(str(step.get('title', '')) + ' ' + str(step.get('outcome', '')) for step in steps).lower()
            actions = rubric['cases'].get(row['id'], [])
            covered = sum(any(term.lower() in text for term in alternatives) for alternatives in actions)
            availability = case_map[row['id']]['request'].get('availability', {})
            scheduled = sum(step.get('scheduled_start') is not None or step.get('scheduled_end') is not None
                            for step in steps)
            result = dict(model=model, id=row['id'], required_actions=len(actions), raw_covered=covered,
                          effective_covered=covered if row['status'] == 'ok' else 0,
                          raw_steps=len(steps), action_object_outcome_present=sum(
                              bool(step.get('title')) and bool(step.get('outcome')) for step in steps),
                          unsupported_slots=scheduled if not availability.get('available_windows') else 0,
                          contract_valid=row['status'] == 'ok')
            rows.append(result)
            selected.append(result)
        if selected:
            summary.append(dict(model=model,
                raw_required_actions_covered=sum(row['raw_covered'] for row in selected),
                required_actions=sum(row['required_actions'] for row in selected),
                effective_actions_covered=sum(row['effective_covered'] for row in selected),
                raw_steps=sum(row['raw_steps'] for row in selected),
                action_object_outcome_present=sum(row['action_object_outcome_present'] for row in selected),
                unsupported_scheduled_steps=sum(row['unsupported_slots'] for row in selected)))
    return dict(summary=summary, rows=rows, review_note='关键词只辅助复核明确动作，不是未知任务的通用参考步骤；原始与可用草案分别统计。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    result = score(json.loads(args.report.read_text()),
                   json.loads((args.evidence / 'decomposition-rubric.json').read_text()),
                   json.loads((args.evidence / 'cases.json').read_text()))
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
