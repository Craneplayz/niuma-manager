"""v0.3.1 structure + semantic checks, offline; not a file reader or auth service.

Schema bundles use only local JSON pointers. Call check_request/check_pair/
check_job as well: JSON Schema alone cannot establish cross-record invariants.
"""
import copy
import hashlib
import ipaddress
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from jsonschema import Draft202012Validator, FormatChecker

if __package__:
    from . import check_v03_examples as previous
    from .build_v031_bundle import ENTRIES
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from contracts import check_v03_examples as previous
    from contracts.build_v031_bundle import ENTRIES

ROOT = Path(__file__).resolve().parent
BUNDLE = json.loads((ROOT / 'schemas/bundle-v0.3.1.schema.json').read_text(encoding='utf-8'))
FORMATS = FormatChecker()
if 'date-time' not in FORMATS.checkers:
    raise RuntimeError('Install contracts/requirements-dev.txt to enable date-time validation.')
require = previous.require
PUBLIC_QUERY_KEYS = {'id', 'page', 'view', 'mode', 'method', 'context', 'courseid', 'handle',
                     'contentid', 'announcementid', 'p', 'lang'}
RETRY_CODES = {'agent_timeout', 'agent_unavailable', 'file_read_failed', 'invalid_agent_output'}
TERMINAL = {'succeeded', 'partial_failed', 'failed'}
SUPPORTED_MIMES = {'image/jpeg', 'image/png', 'application/pdf',
                   'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


def root_schema(entry):
    """Return an independently usable root schema; no custom registry required."""
    document = copy.deepcopy(BUNDLE)
    document.pop('oneOf')
    document['$ref'] = '#/$defs/' + ENTRIES[entry]
    return document


def schema(kind, value, definition=None):
    entry = definition if kind == 'wechat-import' else kind
    require(entry in ENTRIES, 'schema_entry_required')
    Draft202012Validator(root_schema(entry), format_checker=FORMATS).validate(value)


def clean_metadata(value):
    return not any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value)


def check_source_url(value):
    if value is None: return
    require(isinstance(value, str) and len(value) <= 2048 and clean_metadata(value)
            and not any(c.isspace() for c in value) and '\\' not in value, 'unsafe_source_url')
    try:
        url = urlsplit(value)
        host = (url.hostname or '').rstrip('.').encode('idna').decode('ascii').lower()
        require(url.scheme in ('http', 'https') and host and url.username is None
                and url.password is None and not url.fragment
                and url.port in (None, 443 if url.scheme == 'https' else 80), 'unsafe_source_url')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            require('.' in host and not host.endswith(('.localhost', '.local', '.localdomain', '.internal',
                    '.intranet', '.lan', '.home', '.onion', '.invalid'))
                    and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label) for label in host.split('.'))
                    and re.fullmatch(r'[a-z][a-z0-9-]*', host.split('.')[-1]), 'unsafe_source_url')
        else:
            require(address.is_global and not address.is_multicast
                    and (not getattr(address, 'ipv4_mapped', None) or address.ipv4_mapped.is_global), 'unsafe_source_url')
        # Only stable navigation query keys are allowed. All authentication,
        # signing/expiry parameters and unknown keys are rejected, not stripped.
        query = parse_qsl(url.query, keep_blank_values=True, max_num_fields=20)
        keys = [key.casefold().replace('_', '').replace('-', '') for key, _ in query]
        require(len(keys) == len(set(keys)) and all(key in PUBLIC_QUERY_KEYS for key in keys), 'unsafe_source_url')
        require(all(len(v) <= 256 and clean_metadata(v) for _, v in query), 'unsafe_source_url')
        require(not re.search(r'%(?:0[0-9a-f]|1[0-9a-f]|7f)', value, re.I), 'unsafe_source_url')
    except (ValueError, UnicodeError) as error:
        raise ValueError('unsafe_source_url') from error


def check_file_name(value):
    require(bool(value) and len(value) <= 255 and clean_metadata(value)
            and value not in ('.', '..') and value[-1] not in ' .'
            and not any(c in value for c in '<>:"/\\|?*')
            and not re.search(r'%(?:2e|2f|5c|25|0[0-9a-f]|1[0-9a-f]|7f)', value, re.I)
            and not re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])', value.split('.')[0], re.I),
            'unsafe_file_name')


def check_resolved_source_url(value, addresses):
    """Before any actual fetch, check ALL freshly resolved A/AAAA addresses.

    This does not resolve DNS or fetch: caller must pin the validated address
    at connect time and revalidate redirects to prevent DNS rebinding.
    """
    check_source_url(value)
    require(value is not None and bool(addresses), 'unsafe_source_resolution')
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError('unsafe_source_resolution') from error
        require(address.is_global and not address.is_multicast
                and (not getattr(address, 'ipv4_mapped', None) or address.ipv4_mapped.is_global),
                'unsafe_source_resolution')


def json_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise ValueError('invalid_json') from error


def check_hints(value):
    if value is None: return
    require(len(json_bytes(value)) <= 16384, 'hints_too_large')
    count = 0
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop(); count += 1
        require(count <= 256, 'too_many_hint_nodes')
        if isinstance(item, (dict, list)):
            require(depth <= 4 and len(item) <= 32, 'hints_too_deep_or_wide')
            if isinstance(item, dict):
                require(all(isinstance(k, str) and len(k) <= 64 and clean_metadata(k) for k in item), 'invalid_hint_key')
            stack.extend((v, depth + 1) for v in (item.values() if isinstance(item, dict) else item))
        elif isinstance(item, str): require(len(item) <= 4096, 'hint_text_too_long')
        elif isinstance(item, float): require(math.isfinite(item), 'invalid_json')


def check_request(request):
    require(len(json_bytes(request)) <= 2_000_000, 'request_too_large')
    # Bound arbitrary hint trees before recursively validating their structure.
    if isinstance(request, dict) and isinstance(request.get('records'), list):
        for record in request['records']:
            if isinstance(record, dict): check_hints(record.get('upstream_hints'))
    previous.check_request(request, validate_schema=schema)
    for r in request['records']:
        for source in [r] + r['context']:
            check_source_url(source['source_url'])
            for name in ('source_label', 'source_item_id'):
                require(source.get(name) is None or clean_metadata(source[name]), 'invalid_source_metadata')
        for asset in r['attachments']:
            check_file_name(asset['file_name'])
            if asset['mime_type'] not in SUPPORTED_MIMES:
                require(asset['status'] == 'unsupported' and any(e['code'] == 'unsupported_type'
                        for e in asset['errors']), 'unsupported_content_not_reported')


def check_pair(request, result):
    check_request(request)
    require(len(json_bytes(result)) <= 2_000_000, 'result_too_large')
    previous.check_pair(request, result, validate_schema=schema)
    for candidate in result['tasks']:
        require(not any(e['field'] == 'attachment_file' for e in candidate['evidence'])
                or candidate['needs_review'], 'model_evidence_requires_review')


def check_job(request, job):
    check_request(request)
    previous.check_job(request, job, validate_schema=schema)
    if job['result'] is not None: check_pair(request, job['result'])


def check_asset_ack(value):
    schema('wechat-import', value, 'asset_ack')
    check_file_name(value['file_name'])


def check_asset_metadata(asset, receipt):
    """After backend ownership checks, compare claimed versus stored metadata."""
    check_asset_ack(receipt)
    require(all(asset[k] == receipt[k] for k in ('file_ref', 'sha256', 'mime_type', 'size_bytes')),
            'asset_metadata_mismatch')


def request_fingerprint(request):
    check_request(request)
    return hashlib.sha256(json_bytes(request)).hexdigest()


def check_replay(saved, replay):
    """Caller must already scope both requests to the SAME authenticated account."""
    require(saved['request_id'] == replay['request_id'] and request_fingerprint(saved) == request_fingerprint(replay),
            'request_conflict')


def retryable_records(result):
    failed = {r['input_id'] for r in result['record_results'] if r['status'] in ('failed', 'partial_success')}
    return {e['input_id'] for e in result['errors'] if e['input_id'] in failed and e['retryable']
            and e['stage'] in ('read', 'recognize') and e['code'] in RETRY_CODES}


def check_job_transition(request, before, after, *, retry_id=None, retry_record_ids=()):
    """Reference state-machine check; storage locks/replay history remain #96's job."""
    check_job(request, before); check_job(request, after)
    require(before['job_id'] == after['job_id'] and before['request_id'] == after['request_id'], 'job_identity_changed')
    old, new = before['status'], after['status']
    if retry_id is not None:
        schema('wechat-import', {'retry_id': retry_id}, 'retry')
        selected = set(retry_record_ids)
        require(old in ('failed', 'partial_failed') and new == 'queued' and selected
                and len(selected) == len(retry_record_ids)
                and selected <= retryable_records(before['result']), 'invalid_job_state')
        failures = {r['input_id'] for r in before['result']['record_results'] if r['status'] in ('failed', 'partial_success')}
        expected = dict(total_records=len(request['records']), finished_records=len(request['records']) - len(selected),
                        failed_records=len(failures - selected))
        require(after['progress'] == expected, 'retry_progress_mismatch')
        return
    require(not retry_record_ids, 'retry_id_required')
    if old in TERMINAL:
        require(before == after, 'terminal_result_changed')
        return
    require(new in ({'queued', 'running', 'failed'} if old == 'queued' else {'running'} | TERMINAL), 'invalid_job_state')
    if old == new == 'queued':
        require(before == after, 'queued_job_changed')
    require(all(after['progress'][k] >= before['progress'][k] for k in ('finished_records', 'failed_records')),
            'progress_regressed')


def check_retry_result(request, before, after, retry_record_ids):
    check_pair(request, before); check_pair(request, after)
    selected = set(retry_record_ids)
    require(selected and len(selected) == len(retry_record_ids) and selected <= retryable_records(before), 'invalid_job_state')
    for field, identity in [('record_results', 'input_id'), ('errors', 'input_id')]:
        require([v for v in before[field] if v[identity] not in selected] ==
                [v for v in after[field] if v[identity] not in selected], 'successful_result_changed')
    old_tasks = {t['candidate_id']: t for t in before['tasks']}
    new_tasks = {t['candidate_id']: t for t in after['tasks']}
    require(all(new_tasks.get(key) == value for key, value in old_tasks.items()), 'successful_candidate_changed')


def main():
    for entry in ENTRIES: Draft202012Validator.check_schema(root_schema(entry))
    cases = json.loads((ROOT / 'examples/text-attachments-v031-cases.json').read_text(encoding='utf-8'))
    for case in cases: check_pair(case['request'], case['expected'])
    request = json.loads((ROOT / 'source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
    result = json.loads((ROOT / 'text-to-app/text-attachments-v031-output.json').read_text(encoding='utf-8'))
    check_pair(request, result)
    for job in json.loads((ROOT / 'source-to-app/wechat-import-v031-examples.json').read_text(encoding='utf-8')):
        check_job(request, job)
    check_asset_ack(json.loads((ROOT / 'source-to-app/wechat-asset-v031-example.json').read_text(encoding='utf-8')))
    print('PASS: 7 standalone entries, %d synthetic pairs and job/asset examples (v0.3.1).' % (len(cases) + 1))


if __name__ == '__main__': main()
