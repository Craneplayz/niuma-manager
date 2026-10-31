"""Consent-gated email policy and background job orchestration.

email_import_store owns schema and durable job storage. email_import_routes
owns HTTP validation/serialization and is loaded only when routing a request.
This facade preserves app.py and existing recognition-guard entry points.
"""
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
import heapq
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from contextlib import nullcontext
from pathlib import Path

import email_source
import email_header_cache
import recognition
import source_sync
import email_telemetry
import email_scheduler
import email_mailbox_pool
import email_db_gate
from email_import_store import (
    initialize, finish_initialization, counts as _counts,
    save as _save, bump_list_revision as _bump_list_revision,
    load_job as _load_job,
    insert_job as _insert_job, connection as _connection,
    job_states as _job_states, pending_review_counts as _pending_review_counts,
    begin as _begin, item_counts,
)
from server import model_queue
from server import recognition_diagnostics as diagnostics
from contracts.recognition_errors import describe
from text_contract import validate_request, validate_result

CONSENT_VERSION = 'email-body-recognition/1'
_workers = {}
_lock = threading.Lock()
_executor=ThreadPoolExecutor(max_workers=60,thread_name_prefix='email-import')


def _max_jobs():
    import os
    try: value = int(os.environ.get('NIUMA_EMAIL_MAX_JOBS', '60'))
    except (TypeError, ValueError): value = 60
    return max(1, min(60, value))


def _check_admission(db, ApiError, database):
    database_path = database if isinstance(database, (str, Path)) else getattr(database, 'name', None)
    if (not database_path or str(database_path) == ':memory:'
            or not email_scheduler.ready_process(database_path)
            or not email_scheduler.is_current_owner(db)):
        error = ApiError(503, '邮件处理服务暂时不可用，请稍后重试')
        error.retry_after = 5
        raise error
    admitted = {(row['user_id'],row['job_id']) for row in db.execute(
        "SELECT user_id,job_id FROM email_import_jobs WHERE state IN ('queued','running')")}
    with _lock:
        # A stopped job may still be draining an unavoidable in-flight provider
        # request. Count it until its coordinator exits, so stop/start churn
        # cannot bypass admission and grow the executor backlog.
        admitted.update((user,job) for (path,user,job) in _workers
                        if Path(path).resolve() == Path(database_path).resolve())
    if len(admitted) >= _max_jobs():
        error = ApiError(429, '邮件处理队列繁忙，请稍后重试')
        error.retry_after = 5
        raise error


def admission_context(path, method, database):
    user_write = method == 'POST' and (path.startswith('/api/sources/email/jobs') or
        path in ('/api/sources/email/preferences','/api/sources/email/exclusions'))
    return email_db_gate.write(database, urgent=True) if user_write else nullcontext()


def _credential_key(credential):
    return hashlib.sha256(credential['address'].encode() + b'\0' +
        credential['username'].encode() + b'\0' + credential['secret']).hexdigest()


class _Stopped(Exception):
    pass


class _Excluded(Exception):
    pass


def _guard_job(db, uid, job_id, input_id=None):
    if not email_scheduler.is_current_owner(db):
        raise ValueError('email scheduler ownership changed')
    row = db.execute('SELECT snapshot FROM email_import_jobs WHERE user_id=? AND job_id=?',
                     (uid, job_id)).fetchone()
    if not row:
        raise _Stopped()
    job = json.loads(row['snapshot'])
    if job.get('stop') or job['state'] not in ('queued', 'running'):
        raise _Stopped()
    _require_consent(db, uid)
    credential = db.execute('SELECT address,username,secret FROM email_source_credentials WHERE user_id=?',
                            (uid,)).fetchone()
    if not credential or job.get('credential') != _credential_key(credential):
        raise ValueError('mailbox connection changed')
    if input_id:
        prefix = email_source._identity(credential['address'], 'x', 1).rsplit(':', 2)[0] + ':'
        if not re.fullmatch(re.escape(prefix) + r'\d+:\d+', input_id):
            raise ValueError('invalid mailbox message identity')
        if db.execute('SELECT 1 FROM email_import_exclusions WHERE user_id=? AND input_id=? AND excluded=1',
                      (uid, input_id)).fetchone():
            raise _Excluded()
    return job, credential


def _guard_path(path, uid, job_id, input_id=None):
    with _connection(path) as db:
        return _guard_job(db, uid, job_id, input_id)


def _prefs(db, uid):
    row = db.execute('SELECT * FROM email_import_preferences WHERE user_id=?', (uid,)).fetchone()
    mailbox=db.execute('SELECT address FROM email_source_credentials WHERE user_id=?',(uid,)).fetchone()
    granted=bool(row and mailbox and row['mailbox']==mailbox['address'] and row['consent_version']==CONSENT_VERSION)
    return {'consent_granted': granted,
            'process_all': bool(row and row['process_all']),
            'consent_version': row['consent_version'] if granted else None,
            'consented_at': row['consented_at'] if granted else None}


def _public_failure(error):
    if not error:
        return None
    return {key: error[key] for key in
            ('code', 'message', 'retryable', 'failure', 'diagnostic', 'diagnostic_id') if key in error}


def _safe_error(error):
    status = getattr(error, 'status', 502)
    messages = {400: '邮件选择或筛选条件无效', 401: '邮箱登录失败，请检查已保存的凭据',
                409: '邮箱内容或凭据已变化，请重新预览', 422: '邮件正文无法安全读取',
                503: '邮箱或识别服务暂时不可用'}
    return messages.get(status, '邮件处理失败，请稍后重试')


def _request_id(input_id):
    return 'email:import:' + hashlib.sha256(input_id.encode()).hexdigest()[:40]


def _single_email_record(request,input_id):
    records=request.get('records') if isinstance(request,dict) else None
    if not isinstance(records,list): raise ValueError('邮件正文快照不可用，请重新导入该邮件')
    matches=[record for record in records if isinstance(record,dict) and record.get('input_id')==input_id]
    if len(matches)!=1 or matches[0].get('platform')!='email':
        raise ValueError('邮件正文快照不可用，请重新导入该邮件')
    return matches[0]


def current_policy_version():
    """Return current opaque policy version when a usable model config exists."""
    try:
        from ddl_text.codex_client import live_options
        from ddl_text.config import email_model_settings
        from ddl_text.policy_version import text_policy_manifest
        options=live_options(recognition.model_env_path(recognition.ROOT))
        if not options['key'].strip(): return None
        settings=email_model_settings(options)
        policy=text_policy_manifest(settings,provider='qiniu' if options['provider']=='qiniu' else 'codex')
        return policy.get('version') or policy.get('policy_version')
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        return None


_POLICY_UNSET = object()

def policy_cache_metadata(db, uid, input_ids, current=_POLICY_UNSET):
    """Metadata-only cache projection. Never exposes model settings or credentials."""
    if current is _POLICY_UNSET: current=current_policy_version()
    output={}
    for input_id in input_ids:
        cache=db.execute('SELECT request_id,saved_policy_version FROM email_recognition_cache WHERE user_id=? AND input_id=?',(uid,input_id)).fetchone()
        if cache is None:
            _state,_excluded,_count,legacy_id,_error=_item_state(db,uid,input_id)
            if legacy_id:
                policy=db.execute('SELECT policy FROM recognition_record_policies WHERE user_id=? AND request_id=? AND input_id=?',(uid,legacy_id,input_id)).fetchone()
                saved=None
                if policy and policy['policy']:
                    try: saved=json.loads(policy['policy']).get('version')
                    except (ValueError,TypeError,AttributeError): pass
                cache={'request_id':legacy_id,'saved_policy_version':saved}
        saved=cache['saved_policy_version'] if cache else None
        status='unavailable' if current is None else ('unknown' if saved is None else ('current' if saved==current else 'outdated'))
        output[input_id]=dict(policy_status=status,saved_policy_version=saved,current_policy_version=current)
    return output


def _recognition_updates(db, uid, range_spec, snapshot_id, current=_POLICY_UNSET):
    address=_email_address(db,uid)
    ids=email_header_cache.snapshot_inputs(db,uid,address,range_spec,snapshot_id)
    if current is _POLICY_UNSET: current=current_policy_version()
    successful=[input_id for input_id in ids if _item_state(db,uid,input_id)[0] in ('has_tasks','no_tasks')]
    metadata=policy_cache_metadata(db,uid,successful,current=current)
    outdated=[x for x in successful if metadata[x]['policy_status']=='outdated']
    unknown=[x for x in successful if metadata[x]['policy_status']=='unknown']
    token=hashlib.sha256(json.dumps([snapshot_id,current,outdated,unknown],separators=(',',':')).encode()).hexdigest()
    return dict(available=current is not None,outdated_count=len(outdated),unknown_count=len(unknown),
                policy_version=current,selection_token=token),outdated,unknown


def _item_state(db, uid, input_id):
    if db.execute('SELECT 1 FROM email_import_exclusions WHERE user_id=? AND input_id=? AND excluded=1', (uid,input_id)).fetchone():
        return 'excluded', True, None, None, None
    pointer=db.execute('SELECT request_id FROM email_recognition_cache WHERE user_id=? AND input_id=?',(uid,input_id)).fetchone()
    request_id=pointer['request_id'] if pointer else _request_id(input_id)
    row = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?', (uid,request_id)).fetchone()
    if not row and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_versions'").fetchone():
        credential=db.execute('SELECT address FROM email_source_credentials WHERE user_id=?',(uid,)).fetchone()
        if credential:
            account=hashlib.sha256(credential['address'].encode()).hexdigest()
            legacy=db.execute('SELECT request FROM source_versions WHERE user_id=? AND account=? AND input_id=?',(uid,account,input_id)).fetchone()
            if legacy:
                old=json.loads(legacy['request'])
                row=db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,old['request_id'])).fetchone()
                if row: request_id=old['request_id']
    if row:
        result = json.loads(row['result'])
        if result['status'] in ('success','no_tasks'):
            count = len(result.get('tasks', []))
            return ('has_tasks' if count else 'no_tasks'), False, count, request_id, None
        if result['status'] in ('failed','partial_success'):
            return 'failed', False, len(result.get('tasks', [])), request_id, '识别失败，可重试'
    return 'unprocessed', False, None, None, None


def _message_states(db, uid, input_ids):
    """Combine recognition cache and latest durable progress in one place."""
    durable = _job_states(db, uid, input_ids)
    result = {}
    for input_id in input_ids:
        state, excluded, count, request_id, error = _item_state(db, uid, input_id)
        saved = durable.get(input_id)
        if saved and not excluded and state not in ('has_tasks', 'no_tasks') and saved['state'] != 'excluded':
            state = saved['state']
            count, request_id, error = saved['task_count'], saved['request_id'], saved['error']
        result[input_id] = dict(state=state, excluded=excluded, task_count=count,
                                request_id=request_id, error=error,
                                recognition_error=saved.get('recognition_error') if saved and state == 'failed' else None)
    pending = _pending_review_counts(db, uid, input_ids)
    for input_id, item in result.items():
        item['pending_review_count'] = pending.get(input_id, 0)
        item['needs_attention'] = not item['excluded'] and (
            item['state'] in ('unprocessed', 'failed', 'interrupted') or item['pending_review_count'] > 0)
    return result


EMAIL_LIST_FILTERS={'all','attention','unprocessed','pending_review','has_tasks','no_tasks','failed','excluded'}
EMAIL_LIST_SORTS={'attention','newest','oldest'}

def _email_list_selection(db,uid,filter_name,sort_name):
    address=_email_address(db,uid)
    def select(uidvalidity,uids):
        input_ids=[email_source._identity(address,uidvalidity,x) for x in uids]
        states=_message_states(db,uid,input_ids)
        eligible=[]
        for uid_value in uids:
            input_id=email_source._identity(address,uidvalidity,uid_value)
            state=states[input_id]['state']; review=states[input_id]['pending_review_count']
            keep=(filter_name=='all' or
                filter_name=='attention' and states[input_id]['needs_attention'] or
                filter_name=='unprocessed' and state=='unprocessed' or
                filter_name=='pending_review' and review>0 and not states[input_id]['excluded'] or
                filter_name=='has_tasks' and state=='has_tasks' or
                filter_name=='no_tasks' and state=='no_tasks' or
                filter_name=='failed' and state in ('failed','interrupted') or
                filter_name=='excluded' and states[input_id]['excluded'])
            if keep: eligible.append((uid_value,input_id,states[input_id]['needs_attention']))
        if sort_name=='oldest': eligible.sort(key=lambda x:x[0])
        elif sort_name=='newest': eligible.sort(key=lambda x:x[0],reverse=True)
        elif sort_name=='attention': eligible.sort(key=lambda x:(states[x[1]]['excluded'],not x[2],-x[0]))
        return [x[0] for x in eligible]
    return select


def _email_address(db,uid):
    row=db.execute('SELECT address FROM email_source_credentials WHERE user_id=?',(uid,)).fetchone()
    return row['address'] if row else ''


def recognition_guard(db, uid, request, phase):
    """Guard all email recognition transports; save phase runs under IMMEDIATE lock."""
    _require_consent(db, uid)
    for record in request.get('records',[]):
        if record.get('platform') != 'email': continue
        row=db.execute('SELECT address FROM email_source_credentials WHERE user_id=?',(uid,)).fetchone()
        if not row: raise ValueError('邮箱凭据未配置')
        prefix=email_source._identity(row['address'],'x',1).rsplit(':',2)[0]+':'
        input_id=record.get('input_id','')
        if not input_id.startswith(prefix): raise ValueError('邮件来源不属于当前邮箱')
        if db.execute('SELECT 1 FROM email_import_exclusions WHERE user_id=? AND input_id=? AND excluded=1',(uid,input_id)).fetchone():
            raise ValueError('该邮件已排除')
        cached = _item_state(db, uid, input_id)
        if phase == 'model' and not getattr(recognition._EXECUTION,'email_reprocess',False) and cached[0] in ('has_tasks','no_tasks'):
            raise ValueError('该邮件已有识别缓存')


def _require_consent(db, uid):
    if not _prefs(db,uid)['consent_granted']:
        raise ValueError('请先在邮箱设置中同意读取邮件正文并发送至识别服务')


def _public_job(job):
    return {'job_id':job['job_id'],'state':job['state'],'counts':job['counts'],
            'items':[{key:item.get(key) for key in ('input_id','state','task_count','request_id','error','recognition_error')}
                     for item in job['items']], 'kind':job.get('kind','import'), 'error':job.get('error'),
            'revision':int(job.get('revision',0)), 'waiting_reason':job.get('waiting_reason')}


def _public_job_summary(job):
    waiting = job.get('waiting_reason')
    if waiting not in ('global_capacity', 'account_capacity', 'provider_backoff', 'provider_budget', 'model_capacity'):
        waiting = None
    return {'job_id': job['job_id'], 'state': job['state'], 'counts': job['counts'],
            'kind': job.get('kind', 'import'), 'revision': int(job.get('revision', 0)),
            'queued_at': job.get('queued_at'), 'waiting_reason': waiting}


def _update_job(path, uid, job_id, update, allow_terminal=False):
    # Serialize snapshot reads with writes so a progress update cannot erase Stop.
    with _connection(path, write=True) as db:
        if not email_scheduler.is_current_owner(db):
            return None
        row = db.execute('SELECT snapshot FROM email_import_jobs WHERE user_id=? AND job_id=?',
                         (uid, job_id)).fetchone()
        if not row:
            return None
        job = _load_job(db, uid, job_id, row['snapshot'])
        if not allow_terminal and (job.get('stop') or job['state'] not in ('queued', 'running')):
            return job
        update(db, job)
        _save(db, uid, job_id, job)
        return job


def _update_item(path, uid, job_id, input_id, new_item, allowed=('queued', 'failed', 'processing', 'interrupted'), check_guard=False):
    """Persist one progress transition without hydrating or rewriting the job snapshot."""
    started = time.monotonic()
    with _connection(path, write=True) as db:
        acquired = time.monotonic()
        if not email_scheduler.is_current_owner(db):
            return None
        row = db.execute('SELECT state,snapshot FROM email_import_jobs WHERE user_id=? AND job_id=?',
                         (uid, job_id)).fetchone()
        if not row:
            return None
        metadata = json.loads(row['snapshot'])
        if metadata.get('stop') or row['state'] not in ('queued', 'running'):
            return None
        if check_guard:
            _guard_job(db, uid, job_id, input_id)
        item_row = db.execute('SELECT ordinal,item FROM email_import_job_items WHERE user_id=? AND job_id=? AND input_id=?',
                              (uid, job_id, input_id)).fetchone()
        if not item_row:
            return None
        old = json.loads(item_row['item'])
        if old.get('state') not in allowed:
            return old
        value = dict(old); value.update(new_item)
        if value.get('state') != 'failed':
            value.pop('recognition_error', None)
        if value == old:
            return old
        counts = dict(metadata.get('counts') or {})
        before, after = item_counts(old), item_counts(value)
        for key in before:
            counts[key] = max(0, int(counts.get(key, 0)) + after[key] - before[key])
        first_result = (value.get('state') in ('has_tasks', 'no_tasks') and
                        not metadata.get('first_result_recorded'))
        if first_result:
            metadata['first_result_recorded'] = True
        metadata['counts'] = counts
        metadata['revision'] = int(metadata.get('revision', 0)) + 1
        db.execute('UPDATE email_import_job_items SET item=? WHERE user_id=? AND job_id=? AND input_id=?',
                   (json.dumps(value, ensure_ascii=False, separators=(',', ':')), uid, job_id, input_id))
        db.execute('UPDATE email_import_jobs SET snapshot=? WHERE user_id=? AND job_id=?',
                   (json.dumps(metadata, ensure_ascii=False), uid, job_id))
        _bump_list_revision(db, uid)
    committed = time.monotonic()
    if first_result:
        elapsed = max(0, (datetime.now().astimezone()-datetime.fromisoformat(metadata['queued_at'])).total_seconds()*1000)
        email_telemetry.record(path, uid, 'first_result', duration_ms=elapsed)
    email_telemetry.record(path, uid, 'db_wait', duration_ms=(acquired-started)*1000)
    email_telemetry.record(path, uid, 'db_write', duration_ms=(committed-acquired)*1000)
    return value


def _waiting_reason(path, uid, job_id, reason):
    with _connection(path, write=True) as db:
        if not email_scheduler.is_current_owner(db):
            return
        row = db.execute('SELECT snapshot FROM email_import_jobs WHERE user_id=? AND job_id=?',
                         (uid, job_id)).fetchone()
        if not row:
            return
        metadata = json.loads(row['snapshot'])
        if metadata.get('stop') or metadata.get('state') not in ('queued', 'running'):
            return
        if metadata.get('waiting_reason') == reason:
            return
        metadata['waiting_reason'] = reason
        metadata['revision'] = int(metadata.get('revision', 0)) + 1
        db.execute('UPDATE email_import_jobs SET snapshot=? WHERE user_id=? AND job_id=?',
                   (json.dumps(metadata, ensure_ascii=False), uid, job_id))
        _bump_list_revision(db, uid)


def _recognize_item(path, uid, job_id, input_id, ApiError, key_path, prefetched_record=None):
    guard = lambda: _guard_path(path, uid, job_id, input_id)
    reservation = min(email_source.MAX_BODY_BYTES, email_mailbox_pool.BODY_BYTES)
    with email_mailbox_pool.body_buffer(reservation, cancel_check=guard):
        return _recognize_item_reserved(path, uid, job_id, input_id, ApiError, key_path, prefetched_record)


def _prepare_bodies(path, uid, job_id, input_ids, ApiError, key_path):
    """Capture a small batch under bounded memory, then release IMAP before dispatch.

    Pending snapshots are private account-scoped source data, not recognition
    results. They allow capacity/backoff retries without repeating a mailbox read.
    """
    job, credential = _guard_path(path, uid, job_id)
    need_fetch = []
    for input_id in input_ids:
        try:
            _guard_path(path, uid, job_id, input_id)
        except _Excluded:
            _update_item(path, uid, job_id, input_id, dict(state='excluded', error=None))
            continue
        with _connection(path) as db:
            state, _, _, request_id, _ = _item_state(db, uid, input_id)
            if state in ('has_tasks', 'no_tasks') and job.get('kind') != 'reprocess':
                continue
            stored = db.execute('SELECT 1 FROM recognition_batches WHERE user_id=? AND request_id=?',
                                (uid, request_id or _request_id(input_id))).fetchone()
            pending = db.execute('SELECT 1 FROM email_import_body_snapshots WHERE user_id=? AND input_id=?',
                                 (uid, input_id)).fetchone()
            if not stored and not pending:
                need_fetch.append(input_id)
    if not need_fetch:
        return input_ids
    def job_guard():
        _guard_path(path, uid, job_id)
    def message_guard(input_id):
        try:
            _guard_path(path, uid, job_id, input_id)
        except _Excluded:
            raise email_source.SkipEmail() from None
    timings = {}; records = []; fetched = False
    reservation = len(need_fetch) * email_source.MAX_BODY_BYTES
    with email_mailbox_pool.body_buffer(reservation, cancel_check=job_guard, count=len(need_fetch)):
        password = source_sync.cipher(key_path, ApiError).decrypt(credential['secret']).decode()
        started = time.monotonic()
        try:
            records, _ = email_source.fetch_selected(credential['address'], credential['username'], password,
                need_fetch, timings=timings, guard=job_guard, message_guard=message_guard,
                body_capacity_reserved=True)
            fetched = bool(records)
        finally:
            parse_ms = timings.get('parse_ms', 0)
            email_telemetry.record(path, uid, 'fetch',
                duration_ms=max(0, (time.monotonic()-started)*1000-parse_ms), success=fetched)
            email_telemetry.record(path, uid, 'parse', duration_ms=parse_ms, success=fetched)
            email_telemetry.record(path, uid, 'imap_connect', duration_ms=timings.get('connect_ms', 0), success=fetched)
        by_id = {record['input_id']: record for record in records}
        for input_id in need_fetch:
            try:
                _guard_path(path, uid, job_id, input_id)
            except _Excluded:
                _update_item(path, uid, job_id, input_id, dict(state='excluded', error=None))
                continue
            record = by_id.get(input_id)
            if record is None:
                _update_item(path, uid, job_id, input_id,
                             dict(state='failed', error='邮件正文无法安全读取'))
                continue
            with _connection(path, write=True) as db:
                try:
                    _guard_job(db, uid, job_id, input_id)
                except _Excluded:
                    continue
                db.execute('INSERT INTO email_import_body_snapshots VALUES(?,?,?) '
                           'ON CONFLICT(user_id,input_id) DO NOTHING',
                           (uid, input_id, json.dumps(record, ensure_ascii=False)))
    ready = []
    for input_id in input_ids:
        with _connection(path) as db:
            row = db.execute('SELECT item FROM email_import_job_items WHERE user_id=? AND job_id=? AND input_id=?',
                             (uid, job_id, input_id)).fetchone()
            if row and json.loads(row['item'])['state'] in ('queued', 'processing'):
                ready.append(input_id)
    return ready


def _recognize_item_reserved(path, uid, job_id, input_id, ApiError, key_path, prefetched_record=None):
    job, credential = _guard_path(path, uid, job_id, input_id)
    with _connection(path) as db:
        state, _, count, request_id, error = _item_state(db, uid, input_id)
        if state in ('has_tasks', 'no_tasks') and job.get('kind')!='reprocess':
            return dict(input_id=input_id, state=state, task_count=count,
                        request_id=request_id, error=None, cached=True)
        source_id=request_id or _request_id(input_id)
        stored = db.execute('SELECT request FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,source_id)).fetchone()
        captured = db.execute('SELECT record FROM email_import_body_snapshots WHERE user_id=? AND input_id=?',(uid,input_id)).fetchone()
        if prefetched_record is None and captured:
            prefetched_record = json.loads(captured['record'])
    if stored and job.get('kind')!='reprocess':
        # A failed model result already has a captured source: retry that source.
        request = json.loads(stored['request'])
    elif stored:
        request=json.loads(stored['request'])
        request=dict(request,records=[_single_email_record(request,input_id)])
        request['request_id']='email:reprocess:'+hashlib.sha256(input_id.encode()).hexdigest()[:24]+':'+secrets.token_hex(8)
    elif job.get('kind')=='reprocess':
        raise ValueError('邮件正文快照不可用，请重新导入该邮件')
    elif prefetched_record is not None:
        request = dict(contract_version='0.2', request_id=_request_id(input_id), records=[prefetched_record])
    else:
        password = source_sync.cipher(key_path, ApiError).decrypt(credential['secret']).decode()
        timings = {}
        started = time.monotonic()
        records = []
        fetched = False
        try:
            records, _ = email_source.fetch_selected(credential['address'], credential['username'],
                password, [input_id], timings=timings,
                guard=lambda: _guard_path(path, uid, job_id, input_id), body_capacity_reserved=True)
            fetched = bool(records)
        finally:
            parse_ms = timings.get('parse_ms', 0)
            email_telemetry.record(path, uid, 'fetch',
                duration_ms=max(0, (time.monotonic()-started)*1000-parse_ms), success=fetched)
            email_telemetry.record(path, uid, 'parse', duration_ms=parse_ms, success=fetched)
        if not records:
            raise email_source.EmailSourceError(422, 'mail body unavailable')
        request = dict(contract_version='0.2', request_id=_request_id(input_id), records=records)
    validate_request(request)
    _guard_path(path, uid, job_id, input_id)

    try:
        # The idle read connection lets shared diagnostics persist independently
        # after the separate result transaction commits or rolls back.
        with _connection(path) as db, diagnostics.capture(db, uid, request['request_id'],
                skip_exceptions=(model_queue.EmailDeferred, _Stopped, _Excluded)):
            return _recognize_request(path, uid, job_id, input_id, request, job, ApiError)
    except model_queue.EmailDeferred as deferred:
        if deferred.reason not in ('provider_backoff', 'provider_budget', 'model_capacity'):
            raise
        with _connection(path, write=True) as db:
            _guard_job(db, uid, job_id, input_id)
            db.execute('INSERT INTO email_import_body_snapshots VALUES(?,?,?) '
                       'ON CONFLICT(user_id,input_id) DO NOTHING',
                       (uid, input_id, json.dumps(_single_email_record(request,input_id), ensure_ascii=False)))
        return dict(input_id=input_id, state='queued', error=None,
                    _wait_reason=deferred.reason, _wait_seconds=deferred.wait_seconds)


def _recognize_request(path, uid, job_id, input_id, request, job, ApiError):
    def cancelled():
        try:
            _guard_path(path, uid, job_id, input_id)
            return False
        except (ValueError, _Stopped, _Excluded):
            return True

    started = time.monotonic()
    recognized = False
    deferred_call = False
    try:
        previous_flag=getattr(recognition._EXECUTION,'email_reprocess',False)
        recognition._EXECUTION.email_reprocess=job.get('kind')=='reprocess'
        recognition._EXECUTION.policy=None
        try:
            with model_queue.background((str(Path(path).resolve()),str(uid),job_id,input_id)), model_queue.cancel_if(cancelled), recognition.telemetry_context(path, uid):
                result = diagnostics.stamp(recognition.recognize(request, ApiError))
        finally:
            recognition._EXECUTION.email_reprocess=previous_flag
        try:
            result = validate_result(request, result)
        except (ValueError, TypeError, KeyError):
            error = ApiError(502, '邮件识别结果不符合契约，未保存候选')
            error.code = 'invalid_agent_output'
            raise error from None
        recognized = result['status'] in ('success', 'no_tasks')
    except model_queue.EmailDeferred:
        deferred_call = True
        raise
    finally:
        if not deferred_call:
            email_telemetry.record(path, uid, 'recognition',
                duration_ms=(time.monotonic()-started)*1000, success=recognized)
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with _connection(path, write=True) as db:
        # The stop/consent/exclusion/credential check and persistence share a lock.
        _guard_job(db, uid, job_id, input_id)
        previous = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?',
                              (uid, request['request_id'])).fetchone()
        cached = bool(previous and json.loads(previous['result'])['status'] in ('success', 'no_tasks'))
        if cached:
            result = json.loads(previous['result'])
        else:
            db.execute('INSERT INTO recognition_batches(user_id,request_id,fingerprint,request,result,demo,attempt_id) VALUES(?,?,?,?,?,0,?) '
                       'ON CONFLICT(user_id,request_id) DO UPDATE SET '
                       'fingerprint=excluded.fingerprint,request=excluded.request,result=excluded.result,attempt_id=excluded.attempt_id',
                       (uid, request['request_id'], fingerprint, json.dumps(request), json.dumps(result), diagnostics.current_id()))
            protected=db.execute('''SELECT 1 FROM recognition_candidates WHERE user_id=?
                AND json_extract(candidate,'$.input_id')=? AND (state!='pending' OR version!=1) LIMIT 1''',(uid,input_id)).fetchone()
            if job.get('kind')=='reprocess' and result['status'] in ('success','no_tasks'):
                db.execute('''DELETE FROM recognition_candidates WHERE user_id=? AND state='pending' AND version=1
                    AND json_extract(candidate,'$.input_id')=?''',(uid,input_id))
            for candidate in (result['tasks'] if result['status'] in ('success','no_tasks') else []):
                if job.get('kind')=='reprocess' and protected:
                    candidate=dict(candidate)
                    candidate['notice_type']='uncertain'
                    candidate['needs_review']=True
                    candidate['review_reasons']=list(dict.fromkeys(candidate.get('review_reasons',[])+[
                        '邮件重新识别时已有受保护的人工处理记录，请人工复核']))
                db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',
                           (secrets.token_hex(16), uid, request['request_id'], json.dumps(candidate)))
            policy = getattr(recognition._EXECUTION, 'policy', None)
            db.execute('INSERT INTO recognition_record_policies VALUES(?,?,?,?) '
                       'ON CONFLICT(user_id,request_id,input_id) DO UPDATE SET policy=excluded.policy',
                       (uid, request['request_id'], input_id, json.dumps(policy) if policy else None))
            if job.get('kind')=='reprocess' and result['status'] in ('success','no_tasks'):
                db.execute('''INSERT INTO email_recognition_cache(user_id,input_id,request_id,saved_policy_version)
                    VALUES(?,?,?,?) ON CONFLICT(user_id,input_id) DO UPDATE SET
                    request_id=excluded.request_id,saved_policy_version=excluded.saved_policy_version''',
                    (uid,input_id,request['request_id'],policy.get('version') if policy else None))
        db.execute('INSERT OR IGNORE INTO recognition_import_records VALUES(?,?,?,?,?,0)',
                   (uid, request['request_id'], job_id, job['queued_at'], '邮箱'))
        db.execute('DELETE FROM email_import_body_snapshots WHERE user_id=? AND input_id=?',(uid,input_id))
    failed = result['status'] not in ('success', 'no_tasks')
    failure = _public_failure(describe((result.get('errors') or [dict(code='invalid_agent_output',retryable=True)])[0])) if failed else None
    return dict(input_id=input_id,
                state='failed' if failed else ('has_tasks' if result['tasks'] else 'no_tasks'),
                task_count=len(result['tasks']), request_id=request['request_id'],
                error='识别失败，可重试' if failed else None, recognition_error=failure, cached=cached)


def _pending_ids(path, uid, job_id):
    """Keyset pages keep dispatch memory independent of imported range size."""
    ordinal = -1
    while True:
        with _connection(path) as db:
            rows = db.execute("""SELECT ordinal,input_id FROM email_import_job_items
                WHERE user_id=? AND job_id=? AND ordinal>? AND json_extract(item,'$.state') IN ('queued','failed')
                ORDER BY ordinal LIMIT 200""", (uid,job_id,ordinal)).fetchall()
        if not rows:
            return
        for row in rows:
            ordinal = row['ordinal']
            yield row['input_id']


def _expand_range(path, uid, job_id, credential, ApiError, key_path, range_spec):
    password = source_sync.cipher(key_path, ApiError).decrypt(credential['secret']).decode()
    with email_telemetry.measure(path, uid, 'headers'):
        ids = email_source.list_input_ids(credential['address'], credential['username'], password, **range_spec)
    for offset in range(0, len(ids), 200):
        with _connection(path, write=True) as db:
            metadata, _ = _guard_job(db, uid, job_id)
            counts = dict(metadata['counts'])
            for ordinal,input_id in enumerate(ids[offset:offset+200], offset):
                state, excluded, count, request_id, error = _item_state(db, uid, input_id)
                state = 'excluded' if excluded else (state if state in ('has_tasks','no_tasks') else 'queued')
                item = dict(input_id=input_id,state=state,task_count=count,request_id=request_id,error=error,
                            cached=state in ('has_tasks','no_tasks'))
                db.execute('INSERT INTO email_import_job_items VALUES(?,?,?,?,?)',
                           (uid,job_id,ordinal,input_id,json.dumps(item,ensure_ascii=False)))
                counts['selected'] += 1
                counts['processed'] += int(state in ('has_tasks','no_tasks'))
                counts['cached'] += int(item['cached'])
                counts['excluded'] += int(excluded)
                counts['pending'] += int(state == 'queued')
            metadata['counts'] = counts
            metadata['revision'] = int(metadata.get('revision',0))+1
            db.execute('UPDATE email_import_jobs SET snapshot=? WHERE user_id=? AND job_id=?',
                       (json.dumps(metadata,ensure_ascii=False),uid,job_id))
            _bump_list_revision(db,uid)


def _run(path, uid, job_id, ApiError, key_path):
    failure = None
    inflight = {}
    pending = deque()
    prepared = deque()
    delayed = []
    retries = {}
    serial = 0
    blocked_until = 0
    provider_blocked_until = 0
    exhausted = False

    def collect(future, input_id):
        nonlocal serial, blocked_until, provider_blocked_until
        try:
            item = future.result()
        except _Excluded:
            item = dict(input_id=input_id,state='excluded',error=None)
        except _Stopped:
            item = dict(input_id=input_id,state='interrupted',error=None)
        except Exception as error:
            with _connection(path) as db:
                failure = diagnostics.finalize_error(db, uid, error)
            item = dict(input_id=input_id,state='failed',error=_safe_error(error),
                        recognition_error=_public_failure(failure))
        reason = item.pop('_wait_reason', None)
        seconds = item.pop('_wait_seconds', 0)
        if reason:
            if reason == 'provider_backoff':
                retries[input_id] = retries.get(input_id,0)+1
                if retries[input_id] > 2:
                    item.update(state='failed',error='模型限流重试已达上限，请稍后重试')
                    reason = None
            if reason:
                ready_at = time.monotonic()+seconds
                serial += 1
                heapq.heappush(delayed,(ready_at,serial,input_id))
                if reason in ('provider_backoff','provider_budget','model_capacity'):
                    if reason != 'model_capacity':
                        provider_blocked_until = max(provider_blocked_until, ready_at)
                    if reason != 'model_capacity' or time.monotonic() < provider_blocked_until:
                        # A still-inflight sibling can enqueue after the initial
                        # cooldown cleanup. Clear its late ticket too; ordinary
                        # capacity waits retain FIFO order outside provider cooldown.
                        recognition._ACTIVE.cancel_background((str(Path(path).resolve()),str(uid),job_id))
                    # Provider quota/cooldown and the shared model pool are
                    # global constraints, not a per-message semantic failure.
                    # Stop prefetching more bodies while that admission is closed.
                    blocked_until = max(blocked_until,ready_at)
                item['provider_retries'] = retries.get(input_id,0)
                _waiting_reason(path,uid,job_id,reason)
                email_telemetry.record(path,uid,
                    'provider_backoff' if reason == 'provider_backoff' else 'dispatch_wait',
                    duration_ms=seconds*1000,success=False)
        _update_item(path,uid,job_id,input_id,item)

    try:
        with _connection(path, write=True) as db:
            job, credential = _guard_job(db,uid,job_id)
            job['state'] = 'running'
            job['revision'] = int(job.get('revision',0))+1
            db.execute('UPDATE email_import_jobs SET state=?,snapshot=? WHERE user_id=? AND job_id=?',
                       ('running',json.dumps(job,ensure_ascii=False),uid,job_id))
            _bump_list_revision(db,uid)
        email_telemetry.record(path,uid,'queue',duration_ms=max(0,
            (datetime.now().astimezone()-datetime.fromisoformat(job['queued_at'])).total_seconds()*1000))
        if job.get('range_spec') and not job['counts']['selected']:
            _expand_range(path,uid,job_id,credential,ApiError,key_path,job['range_spec'])
        source = iter(_pending_ids(path,uid,job_id))
        concurrency = email_scheduler.limits()[1]
        batch_limit = max(1,min(5,concurrency,email_mailbox_pool.BODY_PREFETCH,
                              email_mailbox_pool.BODY_BYTES//email_source.MAX_BODY_BYTES))

        def dispatch():
            nonlocal exhausted, serial, blocked_until
            if time.monotonic() < blocked_until:
                return
            while len(inflight) < concurrency:
                if not prepared:
                    # Refill a small batch independently of the number of newly
                    # freed model slots. Keep at most one batch of ready IDs.
                    while len(pending) < batch_limit and not exhausted:
                        try: pending.append(next(source))
                        except StopIteration: exhausted = True
                    ids = [pending.popleft() for _ in range(min(batch_limit,len(pending)))]
                    if not ids:
                        return
                    capacity_wait_started = time.monotonic()
                    try:
                        prepared.extend(_prepare_bodies(path,uid,job_id,ids,ApiError,key_path))
                    except TimeoutError as error:
                        if str(error) not in ('body capacity wait expired', 'mailbox capacity wait expired'):
                            raise
                        # Local capacity contention is temporary, not a mail
                        # recognition failure. Requeue the bounded batch and
                        # retry after a short pause without re-reading captured
                        # bodies that may already have been saved.
                        ready_at = time.monotonic() + .5
                        for retry_id in reversed(ids):
                            serial += 1
                            heapq.heappush(delayed,(ready_at,serial,retry_id))
                        blocked_until = max(blocked_until,ready_at)
                        _waiting_reason(path,uid,job_id,'mailbox_capacity')
                        email_telemetry.record(path,uid,'dispatch_wait',
                            duration_ms=(time.monotonic()-capacity_wait_started)*1000,success=False)
                        return
                    if not prepared:
                        continue
                input_id = prepared.popleft()
                try:
                    _guard_path(path,uid,job_id,input_id)
                except _Excluded:
                    _update_item(path,uid,job_id,input_id,dict(state='excluded',error=None))
                    continue
                submitted = time.monotonic()
                capacity = email_scheduler.stats()
                if capacity['running'] >= capacity['global_limit']:
                    _waiting_reason(path,uid,job_id,'global_capacity')
                def on_start(input_id=input_id,submitted=submitted):
                    _update_item(path,uid,job_id,input_id,dict(state='processing'),
                                 allowed=('queued','failed'),check_guard=True)
                    _waiting_reason(path,uid,job_id,None)
                    email_telemetry.record(path,uid,'dispatch_wait',duration_ms=(time.monotonic()-submitted)*1000)
                future = email_scheduler.dispatcher().submit(uid,_recognize_item,path,uid,job_id,
                    input_id,ApiError,key_path,on_start=on_start)
                inflight[future] = input_id

        while True:
            _guard_path(path,uid,job_id)
            now = time.monotonic()
            while delayed and delayed[0][0] <= now:
                pending.appendleft(heapq.heappop(delayed)[2])
            dispatch()
            if not inflight:
                if exhausted and not pending and not prepared and not delayed:
                    break
                # Only the lightweight per-job coordinator waits. No model,
                # dispatcher unit, IMAP session or body buffer is held here.
                time.sleep(.05)
                continue
            done,_ = wait(tuple(inflight),timeout=.1,return_when=FIRST_COMPLETED)
            for future in done:
                collect(future,inflight.pop(future))
    except _Stopped:
        pass
    except Exception as error:
        failure = _safe_error(error)
    finally:
        recognition._ACTIVE.cancel_background((str(Path(path).resolve()),str(uid),job_id))
        for future in tuple(inflight):
            future.cancel()  # Skip queued work; drain only calls already running.
        for future,input_id in tuple(inflight.items()):
            try: collect(future,input_id)
            except BaseException:
                try: future.result()
                except BaseException: pass
        def finish(db,current):
            stopped = current.get('stop') or current['state'] == 'stopped'
            current['waiting_reason'] = None
            for item in current['items']:
                if item['state'] in ('queued','processing'):
                    item.update(state='interrupted' if stopped else 'failed',
                                error=None if stopped else failure or '邮件处理未完成，请重试')
            if stopped:
                current['state'] = 'stopped'
            elif failure:
                current['state'] = 'partial_success' if any(i['state'] in ('has_tasks','no_tasks') for i in current['items']) else 'failed'
                current['error'] = failure
            else:
                bad = any(i['state'] in ('failed','interrupted') for i in current['items'])
                good = any(i['state'] in ('has_tasks','no_tasks') for i in current['items'])
                current['state'] = ('partial_success' if good else 'failed') if bad else 'succeeded'
        try:
            settled = _update_job(path,uid,job_id,finish,allow_terminal=True)
            if settled:
                elapsed = max(0,(datetime.now().astimezone()-datetime.fromisoformat(settled['queued_at'])).total_seconds()*1000)
                email_telemetry.record(path,uid,'job',duration_ms=elapsed,success=settled['state']=='succeeded')
                for stage,key in (('cache_hit','cached'),('excluded','excluded'),('failed','failed')):
                    if settled['counts'][key]:
                        email_telemetry.record(path,uid,stage,quantity=settled['counts'][key])
        finally:
            with _lock:
                _workers.pop((str(path),uid,job_id),None)


def capacity_snapshot():
    result = email_scheduler.capacity_snapshot()
    with _lock:
        result['coordinators'] = len(_workers)
    return result


def _launch(path,uid,job_id,ApiError,key_path):
    if not email_scheduler.ready_process(path) or not email_scheduler.claim_process_owner(path):
        return False
    key=(str(path),uid,job_id)
    with _lock:
        if key in _workers: return
        future=_executor.submit(_run,str(path),uid,job_id,ApiError,key_path)
        _workers[key]=future


def wait_for_jobs(path, timeout=30):
    """Wait for worker persistence and telemetry before disposing a test database."""
    deadline = time.monotonic() + timeout
    while True:
        with _lock:
            futures = [future for (database, _, _), future in _workers.items()
                       if database == str(path)]
        if not futures:
            return
        for future in futures:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('email import workers still running')
            future.result(timeout=remaining)


def route(handler, db, uid, path, body, ApiError, key_path, database):
    """Keep the application entry point stable while isolating HTTP handling."""
    from email_import_routes import route as handle_route
    return handle_route(handler, db, uid, path, body, ApiError, key_path, database)
