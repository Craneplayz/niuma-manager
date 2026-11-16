"""Recompute metrics from a complete receipt matrix, without model calls."""
import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def summarize(evidence, results):
    cases = json.loads((evidence / 'cases.json').read_text())
    vision = [dict(case, module='vision') for case in json.loads((evidence / 'vision-cases.json').read_text())]
    case_map = {case['id']: case for case in cases + vision}
    manifest_path = results / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    selected = set(manifest.get('case_ids', case_map))
    if selected - set(case_map):
        raise ValueError('manifest references unknown cases')
    rows = []
    seen = set()
    for path in sorted(results.glob('*.json')):
        if path.name.startswith('pilot-') or path.name == 'manifest.json':
            continue
        receipt = json.loads(path.read_text())
        if receipt.get('id') not in selected:
            raise ValueError('receipt references an unknown or unselected case')
        identity = (receipt['id'], receipt['model'])
        if identity in seen:
            raise ValueError('duplicate receipt for case/model')
        seen.add(identity)
        if receipt.get('status') not in ('ok', 'error'):
            raise ValueError('incomplete receipt')
        case = case_map[receipt['id']]
        if receipt['module'] != case['module']:
            raise ValueError('receipt module differs from its case')
        if manifest.get('run_id'):
            encoded = json.dumps(case, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
            case_hash = hashlib.sha256(encoded).hexdigest()
            if (receipt.get('run_id') != manifest['run_id'] or receipt.get('case_sha256') != case_hash or
                    manifest.get('case_hashes', {}).get(case['id']) != case_hash):
                raise ValueError('receipt or scoring fixture differs from the manifest')
        output = receipt.get('output')
        expected = case['expected']
        metrics = {}
        valid = receipt['status'] == 'ok'
        module = receipt['module']
        if module == 'recognition':
            valid = valid and isinstance(output, list)
            tasks = output if valid else []
            used = set()
            matched = {}
            for term in expected['terms']:
                index = next((i for i, task in enumerate(tasks)
                              if i not in used and term in task['title'] + ' ' + task['description']), None)
                if index is not None:
                    used.add(index)
                    matched[term] = tasks[index]
            metrics.update(tp=len(matched), fp=len(tasks) - len(matched),
                           fn=len(expected['terms']) - len(matched),
                           count_correct=valid and len(tasks) == expected['count'],
                           binary_correct=valid and bool(tasks) == bool(expected['count']))
            targets = expected.get('targets', [])
            metrics.update(dates_total=len(targets), dates_correct=0, roles_correct=0)
            for target in targets:
                candidate = matched.get(target['term'])
                if candidate:
                    deadline = candidate['deadline']
                    metrics['dates_correct'] += (deadline['value'] == target['value'] and
                                                 deadline['precision'] == target['precision'])
                    metrics['roles_correct'] += deadline['kind'] == 'deadline'
            if case['slice'] == 'nodate':
                metrics['no_invented_submission'] = valid and not any(
                    '提交' in task['title'] + ' ' + task['description'] for task in tasks)
        elif module == 'vision':
            tasks = (output or {}).get('tasks', [])
            targets = expected.get('targets', [])
            metrics.update(count_correct=valid and len(tasks) == expected['count'], dates_total=len(targets))
            metrics['dates_correct'] = sum(valid and any(
                task['deadline']['value'] == target['value'] and
                task['deadline']['precision'] == target['precision'] for task in tasks) for target in targets)
        elif module == 'decomposition':
            metrics.update(contract_valid=valid,
                           usable_plan=valid and output.get('status') in ('ready_to_plan', 'no_split') and
                           bool((output.get('plan') or {}).get('subtasks')),
                           structured_missing_info=valid and output.get('status') == 'needs_clarification',
                           strict_original_state_correct=valid and output['status'] == expected['status'],
                           case_slice=case['slice'])
        elif module == 'estimation':
            estimates = (output or {}).get('estimates', {})
            identifiers = {task['id'] for task in case['request']['tasks']}
            single = next(iter(identifiers)) if len(identifiers) == 1 else None
            complete = valid and set(estimates) == identifiers and all(positive(v) for v in estimates.values())
            metrics.update(minutes=estimates.get(single), positive_complete=complete)
            if expected.get('minutes') is not None:
                metrics['explicit_correct'] = complete and estimates.get(single) == expected['minutes']
        rows.append(dict(**receipt, metrics=metrics))
    if not rows:
        raise ValueError('no receipts found')
    models = manifest.get('models') or sorted({row['model'] for row in rows})
    matrix = {(identifier, model) for identifier in selected for model in models}
    if seen != matrix:
        raise ValueError('receipt matrix is incomplete or contains unexpected models')
    summary = []
    for model in sorted({row['model'] for row in rows}):
        for module in ('recognition', 'vision', 'decomposition', 'estimation'):
            group = [row for row in rows if row['model'] == model and row['module'] == module]
            if not group:
                continue
            item = dict(model=model, module=module, n=len(group), valid=sum(row['status'] == 'ok' for row in group),
                        median_seconds=round(statistics.median(row['seconds'] for row in group), 3),
                        max_seconds=max(row['seconds'] for row in group),
                        calls=sum(len(row['observations']) for row in group))
            for metric in ('tp', 'fp', 'fn', 'dates_total', 'dates_correct', 'roles_correct'):
                if any(metric in row['metrics'] for row in group):
                    item[metric] = sum(row['metrics'].get(metric, 0) for row in group)
            if 'tp' in item:
                tp, fp, fn = item['tp'], item['fp'], item['fn']
                item.update(precision=tp / (tp + fp) if tp + fp else None,
                            recall=tp / (tp + fn) if tp + fn else None,
                            f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None)
            booleans = {key for row in group for key, value in row['metrics'].items() if type(value) == bool}
            for key in sorted(booleans):
                values = [row['metrics'][key] for row in group if key in row['metrics']]
                item[key] = dict(correct=sum(values), total=len(values))
            item['tokens'] = {key: sum(call[key] for row in group for call in row['observations']
                                      if type(call.get(key)) == int)
                              for key in ('input_tokens', 'output_tokens', 'total_tokens')}
            item['missing_usage'] = sum(call.get('total_tokens') is None
                                        for row in group for call in row['observations'])
            if module == 'estimation':
                minutes = {row['id']: row['metrics']['minutes'] for row in group}
                if all(key in minutes for key in ('M3-full', 'M3-progress')):
                    item['progress_pair'] = dict(full=minutes['M3-full'], progress=minutes['M3-progress'])
                for suffix, metric in (('rename', 'rename_invariant'), ('noise', 'noise_invariant')):
                    pair = ('M3-explicit30', 'M3-' + suffix)
                    if all(key in minutes for key in pair):
                        item[metric] = (minutes[pair[0]] == minutes[pair[1]]) if all(
                            positive(minutes[key]) for key in pair) else None
            summary.append(item)
    binary_cases = [case for case in cases if case['module'] == 'recognition' and case['id'] in selected]
    positives = sum(case['expected']['count'] > 0 for case in binary_cases)
    total = len(binary_cases)
    baseline = dict(all_task_positive_records=positives, all_task_records=total,
                    all_positive_accuracy=positives / total if total else None,
                    all_positive_precision=positives / total if total else None,
                    all_positive_recall=1.0 if positives else None)
    return dict(summary=summary, rows=rows, baseline=baseline)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    report = summarize(args.evidence, args.results)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
