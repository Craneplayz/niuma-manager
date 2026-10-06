"""Offline reference checks for synthetic v0.3 contracts; no model/API calls.

This is not an application adapter. Schema checks plus referential invariants
make the examples executable; semantic extraction and authorization are tested
by their owning modules when they implement v0.3.
"""
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent
SCHEMAS = {value['$id']: value for path in ROOT.rglob('*v0.3.schema.json')
           for value in [json.loads(path.read_text(encoding='utf-8'))]}
REGISTRY = Registry().with_resources((uri, Resource.from_contents(value)) for uri, value in SCHEMAS.items())
PREFIX = 'urn:niuma:contracts:'
FORMATS = FormatChecker()
if 'date-time' not in FORMATS.checkers:
    raise RuntimeError('Install contracts/requirements-dev.txt to enable date-time validation.')


def require(condition, code):
    if not condition:
        raise ValueError(code)


def schema(kind, value, definition=None):
    document = SCHEMAS[PREFIX + kind + ':0.3']
    target = document if definition is None else {'$ref': document['$id'] + '#/$defs/' + definition}
    Draft202012Validator(target, registry=REGISTRY, format_checker=FORMATS).validate(value)


def coverage(record, summaries):
    complete = record['text_quality'] in ('complete', 'missing') and all(
        a['status'] == 'complete' for a in summaries)
    uncertain = record['text_quality'] in ('partial', 'summary') or any(
        b['quality'] == 'uncertain' for a in record['attachments'] for b in a.get('blocks', [])) or any(
        a.get('quality') == 'uncertain' for a in summaries)
    return dict(complete=complete, uncertain=uncertain)


def check_attachment_summaries(record, summaries, errors):
    require([a['asset_id'] for a in summaries] == [a['asset_id'] for a in record['attachments']],
            'false_attachment_summary')
    for asset, summary in zip(record['attachments'], summaries):
        if asset.get('content_mode') != 'file':
            expected = dict(asset_id=asset['asset_id'], status=asset['status'], block_count=len(asset['blocks']),
                            uncertain_block_ids=[b['block_id'] for b in asset['blocks'] if b['quality'] == 'uncertain'])
            require(summary == expected, 'false_attachment_summary')
            continue
        require(summary.get('content_mode') == 'file', 'false_attachment_summary')
        if asset['status'] != 'available':
            require(summary['status'] == asset['status'], 'unavailable_file_read')
        cov = summary['coverage']
        read_errors = [e for e in errors if e['asset_id'] == asset['asset_id']
                       and e['stage'] in ('collect', 'upload', 'read')]
        require(bool(read_errors) == (summary['status'] != 'complete'), 'file_read_error_mismatch')
        if not cov['complete']:
            require(cov['unread_locations'] or cov['truncated'], 'unexplained_coverage_gap')


def check_locator(loc, allow_unknown=False):
    require(allow_unknown or loc['page'] is not None or loc['paragraph'] is not None, 'missing_locator')
    if loc['bbox'] is not None:
        left, top, right, bottom = loc['bbox']
        require(loc['page'] is not None and left < right and top < bottom, 'invalid_bbox')


def check_request(request, *, validate_schema=schema):
    validate_schema('request', request)
    require(len(json.dumps(request, ensure_ascii=False).encode('utf-8')) <= 2_000_000, 'request_too_large')
    records = {r['input_id']: r for r in request['records']}
    require(len(records) == len(request['records']), 'duplicate_input_id')
    contexts = {}
    for r in records.values():
        local_ids = [c['input_id'] for c in r['context']]
        require(len(local_ids) == len(set(local_ids)), 'duplicate_context_id')
        for c in r['context']:
            require(c['input_id'] not in records, 'context_overlaps_record')
            require(c['input_id'] not in contexts or contexts[c['input_id']] == c, 'conflicting_context')
            contexts[c['input_id']] = c
        for source in [r] + r['context']:
            if source['timezone'] is not None:
                ZoneInfo(source['timezone'])
            if source['source_url'] is not None:
                url = urlsplit(source['source_url'])
                require(url.scheme in ('https', 'http') and bool(url.hostname)
                        and not url.username and not url.password, 'unsafe_source_url')
        assets = {a['asset_id']: a for a in r['attachments']}
        require(len(assets) == len(r['attachments']), 'duplicate_asset_id')
        length = len(r['text']) + sum(len(c['text']) for c in r['context'])
        for asset in assets.values():
            require('/' not in asset['file_name'] and '\\' not in asset['file_name'], 'private_file_path')
            if asset.get('content_mode') == 'file':
                # Ownership, byte hashes and MIME must be checked by the backend,
                # not inferred from a syntactically valid opaque reference.
                continue
            blocks = {b['block_id']: b for b in asset['blocks']}
            require(len(blocks) == len(asset['blocks']), 'duplicate_block_id')
            length += sum(len(b['text']) for b in blocks.values())
            if asset['status'] != 'complete':
                require(asset['coverage']['unread_locations'] or asset['coverage']['truncated'], 'unexplained_coverage_gap')
            for block in blocks.values():
                check_locator(block['locator'])
        require(length <= 100000, 'record_text_too_large')


def evidence_text(record, evidence):
    sources = {s['input_id']: s for s in [record] + record['context']}
    require(evidence['input_id'] in sources, 'foreign_evidence')
    if evidence['field'] in ('attachment', 'attachment_file'):
        require(evidence['input_id'] == record['input_id'], 'foreign_attachment')
        assets = {a['asset_id']: a for a in record['attachments']}
        require(evidence['asset_id'] in assets, 'unknown_asset')
        asset = assets[evidence['asset_id']]
        if evidence['field'] == 'attachment_file':
            require(asset.get('content_mode') == 'file' and asset['status'] == 'available', 'invalid_file_evidence')
            check_locator(evidence['locator'], allow_unknown=True)
            # Only internal consistency and provenance can be checked here. This
            # quote is MODEL-REPORTED, not a verified substring of known text.
            return evidence['quote']
        require(asset.get('content_mode') != 'file', 'file_without_text_blocks')
        blocks = {b['block_id']: b for b in asset['blocks']}
        require(evidence['block_id'] in blocks, 'unknown_block')
        value = blocks[evidence['block_id']]['text']
    else:
        require(evidence['field'] == 'text' or evidence['input_id'] == record['input_id'], 'context_raw_hint')
        value = sources[evidence['input_id']]
        for part in evidence['field'].split('.'):
            require(isinstance(value, dict) and part in value, 'missing_evidence_field')
            value = value[part]
    require(isinstance(value, str) and evidence['quote'] in value, 'fabricated_quote')
    return value


def check_candidate(record, task, cov, summaries):
    sources = {s['input_id']: s for s in [record] + record['context']}
    source_ids = [s['input_id'] for s in task['sources']]
    require(len(source_ids) == len(set(source_ids)), 'duplicate_source')
    for source in task['sources']:
        require(source['input_id'] in sources, 'foreign_source')
        original = sources[source['input_id']]
        require(source == dict(input_id=original['input_id'], platform=original['platform'],
                url=original['source_url'], label=original['source_label'],
                source_item_id=original.get('source_item_id')), 'fabricated_source')
    supported = set()
    deadline_quotes = []
    for evidence in task['evidence']:
        evidence_text(record, evidence)
        if evidence['field'] == 'attachment_file':
            require(any(a['asset_id'] == evidence['asset_id'] and a['status'] in ('complete', 'partial')
                        for a in summaries), 'unread_file_evidence')
        require(evidence['input_id'] in source_ids, 'evidence_source_missing')
        supported.update(evidence['supports'])
        if 'deadline' in evidence['supports']:
            deadline_quotes.append(evidence['quote'])
    required = {'title'}
    deadline = task['deadline']
    if deadline['status'] not in ('unknown', 'not_provided'):
        required.add('deadline')
    if task['notice_type'] != 'create': required.add('notice_type')
    if task['target_reference'] is not None: required.add('target_reference')
    if task['course_basis'] == 'inferred': required.add('course_or_project')
    if task['prerequisites']: required.add('prerequisites')
    require(required <= supported, 'missing_evidence_support')
    if deadline['raw'] is not None:
        require(any(deadline['raw'] in evidence_text(record, e) for e in task['evidence']), 'fabricated_deadline_raw')
    if 'deadline' in required:
        require(any(deadline['raw'] in q for q in deadline_quotes), 'deadline_quote_missing_raw')
    if task['course_basis'] == 'provided':
        require(task['course_or_project'] == record['course_or_project'], 'fabricated_course')
    if deadline['status'] == 'not_provided':
        require(cov['complete'] and not cov['uncertain'], 'incomplete_no_deadline')
    uncertain = (not cov['complete'] or cov['uncertain'] or task['course_basis'] == 'inferred'
                 or task['notice_type'] == 'uncertain' or deadline['status'] in ('unknown', 'inferred', 'conflict'))
    require(not uncertain or task['needs_review'], 'missing_review')
    if deadline['timezone'] is not None:
        zone = ZoneInfo(deadline['timezone'])
        if deadline['precision'] == 'datetime':
            value = datetime.fromisoformat(deadline['value'].replace('Z', '+00:00'))
            require(value.utcoffset() == value.astimezone(zone).utcoffset(), 'timezone_offset_mismatch')


def check_pair(request, result, *, validate_schema=schema):
    check_request(request, validate_schema=validate_schema)
    validate_schema('result', result)
    require(result['request_id'] == request['request_id'], 'request_id_mismatch')
    records = {r['input_id']: r for r in request['records']}
    for error in result['errors']:
        rid, aid, bid = (error[k] for k in ('input_id', 'asset_id', 'block_id'))
        if rid is None:
            require(aid is None and bid is None and error['stage'] == 'request', 'bad_envelope_error')
            continue
        require(rid in records, 'foreign_error')
        require(error['stage'] != 'request', 'record_error_at_request_stage')
        if aid is not None:
            assets = {a['asset_id']: a for a in records[rid]['attachments']}
            require(aid in assets, 'unknown_error_asset')
            require(bid is None or bid in {b['block_id'] for b in assets[aid].get('blocks', [])}, 'unknown_error_block')
        else:
            require(bid is None, 'error_block_without_asset')
    if any(e['input_id'] is None for e in result['errors']):
        require(result['status'] == 'failed' and not result['tasks'] and not result['record_results'], 'bad_envelope_failure')
        return
    outcomes = {r['input_id']: r for r in result['record_results']}
    require(len(outcomes) == len(result['record_results']) and outcomes.keys() == records.keys(), 'missing_record_result')
    ids = [t['candidate_id'] for t in result['tasks']]
    require(len(ids) == len(set(ids)), 'duplicate_candidate_id')
    require(all(t['input_id'] in records for t in result['tasks']), 'foreign_candidate')
    for rid, outcome in outcomes.items():
        r = records[rid]
        tasks = [t for t in result['tasks'] if t['input_id'] == rid]
        errors = [e for e in result['errors'] if e['input_id'] == rid]
        require(outcome['task_ids'] == [t['candidate_id'] for t in tasks], 'task_ids_mismatch')
        check_attachment_summaries(r, outcome['attachments'], errors)
        expected_coverage = coverage(r, outcome['attachments'])
        if any(e['stage'] == 'recognize' for e in errors):
            expected_coverage['complete'] = False
        require(outcome['coverage'] == expected_coverage, 'false_coverage')
        require(bool(errors) == (outcome['status'] in ('failed', 'partial_success')), 'missing_record_error')
        if outcome['status'] == 'success': require(outcome['coverage']['complete'], 'incomplete_success')
        if outcome['status'] == 'partial_success': require(not outcome['coverage']['complete'], 'false_partial_success')
        for asset in r['attachments']:
            for error in asset['errors']:
                require(any(e['asset_id'] == asset['asset_id'] and e['code'] == error['code']
                            and e['stage'] == error['stage'] for e in errors), 'lost_extraction_error')
        for task in tasks:
            check_candidate(r, task, outcome['coverage'], outcome['attachments'])
    failures = sum(r['status'] == 'failed' for r in outcomes.values())
    partial = any(r['status'] == 'partial_success' for r in outcomes.values())
    expected = ('failed' if failures == len(records) else 'partial_success') if failures else (
        'partial_success' if partial else 'success' if result['tasks'] else 'no_tasks')
    require(result['status'] == expected, 'status_mismatch')


def check_job(request, job, *, validate_schema=schema):
    check_request(request, validate_schema=validate_schema)
    validate_schema('wechat-import', job, 'job')
    require(job['request_id'] == request['request_id'], 'job_request_mismatch')
    p = job['progress']
    require(p['total_records'] == len(request['records']), 'job_total_mismatch')
    require(p['failed_records'] <= p['finished_records'] <= p['total_records'], 'job_progress_mismatch')
    if job['result'] is not None:
        result = job['result']
        check_pair(request, result, validate_schema=validate_schema)
        mapped = {'success': 'succeeded', 'no_tasks': 'succeeded', 'partial_success': 'partial_failed', 'failed': 'failed'}
        require(job['status'] == mapped[result['status']], 'job_status_mismatch')
        failed = sum(r['status'] in ('failed', 'partial_success') for r in result['record_results'])
        if not result['record_results']: failed = len(request['records'])
        require(p['finished_records'] == p['total_records'] and p['failed_records'] == failed, 'job_final_progress_mismatch')


def main():
    for document in SCHEMAS.values(): Draft202012Validator.check_schema(document)
    cases = json.loads((ROOT / 'examples/text-attachments-v03-cases.json').read_text(encoding='utf-8'))
    for case in cases:
        check_pair(case['request'], case['expected'])
    request = json.loads((ROOT / 'source-to-text/text-attachments-v03-request.json').read_text(encoding='utf-8'))
    result = json.loads((ROOT / 'text-to-app/text-attachments-v03-output.json').read_text(encoding='utf-8'))
    check_pair(request, result)
    for job in json.loads((ROOT / 'source-to-app/wechat-import-v03-examples.json').read_text(encoding='utf-8')):
        check_job(request, job)
    schema('wechat-import', json.loads((ROOT / 'source-to-app/wechat-asset-v03-example.json').read_text(encoding='utf-8')), 'asset_ack')
    print('PASS: %d schemas, %d synthetic pairs and import job states; no model calls.' % (len(SCHEMAS), len(cases) + 1))


if __name__ == '__main__':
    main()
