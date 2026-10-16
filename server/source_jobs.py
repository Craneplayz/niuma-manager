"""Durable teaching sync; crawler/model work never holds a SQLite write lock."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from copy import deepcopy

from contracts import source_sync as contract
from server.model_queue import cancel_if
import recognition
import source_sync
import teaching
import usage

CONCURRENCY = 4
RUN_LIMIT = 4
QUIET_RECEIPT_SECONDS = 24 * 60 * 60
_slots = threading.BoundedSemaphore(RUN_LIMIT)
_lock = threading.Lock()
_workers = {}


def stamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection(path):
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    try:
        with db: yield db
    finally: db.close()


def initialize(db, recover=True):
    db.execute('''CREATE TABLE IF NOT EXISTS source_sync_runs(
        user_id TEXT NOT NULL, run_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
        credential TEXT NOT NULL, state TEXT NOT NULL, snapshot TEXT NOT NULL,
        UNIQUE(user_id,operation_id))''')
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS source_sync_one_active
        ON source_sync_runs(user_id) WHERE state IN ('queued','crawling','recognizing')""")
    db.execute('''CREATE TABLE IF NOT EXISTS source_sync_operations(
        user_id TEXT NOT NULL, operation_id TEXT NOT NULL, run_id TEXT NOT NULL,
        signature TEXT NOT NULL, PRIMARY KEY(user_id,operation_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS source_sync_quiet_runs(
        user_id TEXT NOT NULL, run_id TEXT PRIMARY KEY, expires_at REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS source_sync_quiet_expiry ON source_sync_quiet_runs(expires_at,user_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS source_sync_checks(
        user_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)''')
    for row in db.execute('SELECT user_id,operation_id,run_id,snapshot FROM source_sync_runs ORDER BY rowid').fetchall():
        run = json.loads(row['snapshot'])
        db.execute('INSERT OR IGNORE INTO source_sync_operations VALUES(?,?,?,?)',
                   (row['user_id'],row['operation_id'],row['run_id'],
                    operation_signature(run['parent_run_id'],run.get('retry_selection'),run.get('recover_sources',False))))
        if run['state'] in contract.FINAL and 'review' not in run.get('counts', {}):
            compact(db, run)
            run['counts'] = counts(run)
            run['revision'] += 1
            db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?', (json.dumps(run), run['run_id']))
            remember_check(db, run)
        # Backfill names only when stable candidate/task identities reconcile the
        # historical count. Never turn one source label into several task names.
        if run['state'] in contract.FINAL and backfill_items(db, run):
            run['revision'] += 1
            db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?', (json.dumps(run), run['run_id']))
    prune_quiet(db)
    if recover:
        for row in db.execute("SELECT * FROM source_sync_runs WHERE state IN ('queued','crawling','recognizing')").fetchall():
            run = json.loads(row['snapshot'])
            for item in run['records']:
                if item['state'] in ('queued', 'running'): item['state'] = 'interrupted'
            run['error'] = error('interrupted', '服务已重启，已保存结果保留；可继续未完成记录。', True)
            save(db, run, 'interrupted')


def credential(db, uid):
    row = db.execute('SELECT username,secret FROM source_credentials WHERE user_id=?', (uid,)).fetchone()
    return hashlib.sha256(row['username'].encode()+b'\0'+row['secret']).hexdigest() if row else None


def error(code, message, retryable=False):
    return dict(code=code, message=message, retryable=retryable)


def counts(run):
    result = {key: sum(r['state'] == key for r in run['records']) for key in contract.ITEM_STATES}
    result.update(total=len(run['records']), skipped=run['skipped'], added=sum(r['added'] for r in run['records']),
                  review=sum(r.get('review', 0) for r in run['records']), updated=len(run.get('updated_tasks', [])))
    return result


def review_items(db, uid, request_id):
    batch = db.execute('SELECT request,result FROM recognition_batches WHERE user_id=? AND request_id=?', (uid, request_id)).fetchone()
    if not batch: return []
    request, result = json.loads(batch['request']), json.loads(batch['result'])
    cards = None
    if request.get('contract_version') == '0.4':
        projected = teaching.submission.project_result(db, uid, request_id, teaching.result_view(request, result))
        cards = {card['candidate_id'] for card in projected['tasks']}
    return [dict(candidate_id=row['id'], label=card['title'][:200], course=card.get('course_or_project'))
            for row in db.execute('SELECT id,candidate FROM recognition_candidates WHERE user_id=? AND request_id=? AND state="pending"', (uid, request_id))
            for card in [json.loads(row['candidate'])] if cards is None or card['candidate_id'] in cards]


def added_items(db, uid, request_id, candidate_ids=None):
    tasks = source_sync.task_snapshot(db, uid).values()
    targets = {entry.get('storageId'): task for task in tasks for entry in task.get('recognitionHistory', [])
               if entry.get('requestId') == request_id and entry.get('action') == 'create'}
    result = []
    for row in db.execute('SELECT id,candidate,resolution FROM recognition_candidates WHERE user_id=? AND request_id=? AND state="applied"', (uid, request_id)):
        resolution = json.loads(row['resolution'] or '{}')
        if candidate_ids is not None and row['id'] not in candidate_ids: continue
        if resolution.get('action') != 'create' or resolution.get('auto_apply') is not True: continue
        card, task = json.loads(row['candidate']), targets.get(row['id'])
        result.append(dict(candidate_id=row['id'], task_id=task['id'] if task else None,
                           label=str(task['title'] if task else card['title'])[:200],
                           course=task.get('course') if task else card.get('course_or_project')))
    return result


def backfill_items(db, run):
    changed = False
    for item in run['records']:
        for key, count, values in (
            ('added_items', item['added'], added_items), ('review_items', item.get('review', 0), review_items)):
            if key in item: continue
            receipts = values(db, run['user_id'], item['request_id']) if count else []
            if len(receipts) == count:
                item[key] = receipts
                changed = True
    return changed


def useful_record(item):
    return bool(item['added'] or item.get('review') or item['state'] in ('failed', 'incomplete', 'cancelled', 'interrupted'))


def useful_run(run):
    return bool(run.get('updated_tasks') or run['error'] or run['state'] in ('failed', 'interrupted') or
                any(useful_record(item) for item in run['records']))


def compact(db, run):
    succeeded = {item['request_id'] for item in run['records'] if item['state'] == 'succeeded'}
    succeeded.update(old for new, old in run.get('recovery_replacements', {}).items() if new in succeeded)
    if succeeded:
        older_runs = [json.loads(row['snapshot']) for row in db.execute('''SELECT snapshot FROM source_sync_runs WHERE user_id=? AND run_id<>?
            AND state IN ('failed','partial_success','stopped','interrupted') ORDER BY rowid DESC''', (run['user_id'], run['run_id'])).fetchall()]
        # A source can need several fresh snapshots before it becomes complete.
        # Resolve its recorded predecessor chain before updating older receipts.
        for older in older_runs:
            succeeded.update(old for new, old in older.get('recovery_replacements', {}).items() if new in succeeded)
        for older in older_runs:
            recovered = [item for item in older['records'] if item['request_id'] in succeeded and
                         item['state'] in ('failed', 'incomplete', 'cancelled', 'interrupted') and not item.get('recovered')]
            if recovered:
                for item in recovered: item['recovered'] = True
                older['revision'] += 1
                db.execute('UPDATE source_sync_runs SET snapshot=? WHERE run_id=?', (json.dumps(older), older['run_id']))
    for item in run['records']:
        if 'review' not in item or item['state'] == 'succeeded':
            item['review_items'] = review_items(db, run['user_id'], item['request_id'])
            item['review'] = len(item['review_items'])
    run['records'] = [item for item in run['records'] if useful_record(item)]
    run['skipped'] = 0
    if not useful_run(run):
        expires = datetime.fromisoformat(run['updated_at']).timestamp() + QUIET_RECEIPT_SECONDS
        db.execute('INSERT OR REPLACE INTO source_sync_quiet_runs VALUES(?,?,?)', (run['user_id'], run['run_id'], expires))


def remember_check(db, run):
    check = {key: run[key] for key in ('run_id', 'state', 'updated_at', 'trigger')}
    check['changed'] = bool(run.get('updated_tasks') or any(item['added'] or item.get('review') for item in run['records']))
    db.execute('''INSERT INTO source_sync_checks VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET snapshot=excluded.snapshot
        WHERE json_extract(source_sync_checks.snapshot,'$.updated_at') <= json_extract(excluded.snapshot,'$.updated_at')''',
               (run['user_id'], json.dumps(check)))


def prune_quiet(db, uid=None, now=None):
    query = 'SELECT run_id FROM source_sync_quiet_runs WHERE expires_at<=?'
    args = [time.time() if now is None else now]
    if uid is not None: query += ' AND user_id=?'; args.append(uid)
    for row in db.execute(query, args).fetchall():
        db.execute('DELETE FROM source_sync_operations WHERE run_id=?', (row['run_id'],))
        db.execute('DELETE FROM source_sync_runs WHERE run_id=?', (row['run_id'],))
        db.execute('DELETE FROM source_sync_quiet_runs WHERE run_id=?', (row['run_id'],))


def save(db, run, state=None):
    previous = db.execute('SELECT snapshot FROM source_sync_runs WHERE run_id=?', (run['run_id'],)).fetchone()
    previous = json.loads(previous['snapshot']) if previous else {}
    if state: run['state'] = state
    run['revision'] += 1
    run['updated_at'] = stamp()
    if run['state'] in contract.FINAL: compact(db, run)
    run['counts'] = counts(run)
    db.execute('UPDATE source_sync_runs SET state=?,snapshot=? WHERE user_id=? AND run_id=?',
               (run['state'], json.dumps(run), run['user_id'], run['run_id']))
    old_records = {r['request_id']:r for r in previous.get('records',[])}
    for item in run['records']:
        if (item['state'] in ('succeeded','failed','incomplete') and item.get('seconds') is not None
                and old_records.get(item['request_id'],{}).get('state') in ('queued','running')):
            usage.record(db,run['user_id'],'recognition',item['state']!='failed',item['seconds']*1000)
    if previous.get('state') in contract.ACTIVE and run['state'] in contract.FINAL:
        duration=(datetime.fromisoformat(run['updated_at'])-datetime.fromisoformat(run['created_at'])).total_seconds()*1000
        usage.record(db,run['user_id'],'sync',run['state']=='succeeded',duration)
        remember_check(db, run)


def view(run):
    return {k: v for k, v in run.items() if k not in ('user_id', 'retry_selection', 'recover_sources', 'recovery_targets', 'recovery_replacements')}


def can_retry(item):
    return not item.get('recovered') and (item['state'] in ('cancelled', 'interrupted') or (
        item['state']=='failed' and (item['error'] or {}).get('retryable', False)))


def needs_collection(item):
    return item['state'] == 'incomplete' or (item.get('error') or {}).get('code') in ('source_incomplete', 'source_changed', 'source_missing')


def recovery_records(db, uid, old, selection, ApiError):
    selected = set(selection) if selection is not None else {item['request_id'] for item in old['records']}
    if not selected.issubset({item['request_id'] for item in old['records']}): raise ApiError(400,'所选记录不属于本轮同步')
    latest = {}
    for row in db.execute('SELECT snapshot FROM source_sync_runs WHERE user_id=? ORDER BY rowid DESC', (uid,)):
        run = json.loads(row['snapshot'])
        for item in run['records']: latest.setdefault(item['request_id'], run['run_id'])
    records, targets = [], {}
    for original in old['records']:
        rid = original['request_id']
        if rid not in selected or latest.get(rid) != old['run_id'] or original.get('recovered'): continue
        if not (can_retry(original) or needs_collection(original)): continue
        identity = original.get('input_id')
        if not identity:
            row = db.execute('SELECT request FROM teaching_sources WHERE user_id=? AND request_id=?', (uid,rid)).fetchone()
            if not row: row = db.execute('SELECT request FROM recognition_batches WHERE user_id=? AND request_id=?', (uid,rid)).fetchone()
            if row:
                first = json.loads(row['request'])['records'][0]
                identity = first.get('record',first).get('input_id')
        if not identity: raise ApiError(409,'异常来源标识已失效，请从顶部重新获取教学网内容')
        item = deepcopy(original)
        targets[rid] = dict(input_id=identity, needs_collection=needs_collection(original))
        item.update(input_id=identity,state='queued',added=0,review=0,added_items=[],review_items=[],error=None,seconds=None)
        records.append(item)
    if not records: raise ApiError(409,'所选异常已恢复或已有更新的处理记录，请刷新进度')
    return records, targets


def retry_records(db, uid, old, selection, ApiError):
    """Resolve each requested record against its newest attempt, not a stale parent."""
    originals = {r['request_id']: r for r in old['records']}
    selected = set(selection) if selection is not None else set(originals)
    if not selected.issubset(originals): raise ApiError(400, '所选记录不属于本轮同步')
    latest = {}
    for row in db.execute('SELECT snapshot FROM source_sync_runs WHERE user_id=? ORDER BY rowid DESC', (uid,)):
        run = json.loads(row['snapshot'])
        for item in run['records']:
            if item['request_id'] in selected: latest.setdefault(item['request_id'], (run['run_id'],item))
        if selected.issubset(latest): break
    records = []
    for identity, original in originals.items():
        latest_run, latest_item = latest.get(identity, (old['run_id'],original))
        if (identity not in selected or latest_run!=old['run_id'] or
                not can_retry(original) or not can_retry(latest_item)): continue
        stored = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?', (uid, identity)).fetchone()
        if stored:
            result = json.loads(stored['result'])
            if result['status'] in ('success','no_tasks') or teaching.needs_source_completion(result): continue
        try: teaching.resolve_snapshot(db, uid, identity, ApiError)
        except ApiError as exc:
            if exc.status==409: continue  # removed, replaced or already submitted
            raise
        item = deepcopy(original); item.update(state='queued', added=0, review=0, added_items=[], review_items=[], error=None, seconds=None)
        records.append(item)
    if not records: raise ApiError(409, '所选记录已恢复、来源已变化或不可重试；请刷新进度，需要补全内容时重新获取来源')
    return records


def stop(db, run):
    if run['state'] not in contract.ACTIVE: return
    for item in run['records']:
        if item['state']=='queued': item['state']='cancelled'
        elif item['state']=='running': item['state']='interrupted'
    save(db,run,'stopped')


def get(db, uid, run_id, ApiError):
    row = db.execute('SELECT snapshot FROM source_sync_runs WHERE user_id=? AND run_id=?', (uid, run_id)).fetchone()
    if not row: raise ApiError(404, '同步记录不存在')
    return json.loads(row['snapshot'])


def guard(db, uid, run_id, ApiError):
    row = db.execute('SELECT credential,state FROM source_sync_runs WHERE user_id=? AND run_id=?', (uid, run_id)).fetchone()
    if not row or row['state'] not in contract.ACTIVE: raise ApiError(409, '本轮同步已停止或失效')
    if credential(db, uid) != row['credential']: raise ApiError(409, '教学网凭据已变化，请重新同步')


def operation_signature(parent, selection, recover=False):
    value = [parent,sorted(selection) if selection is not None else None]
    if recover: value.append('recover')
    return json.dumps(value)


def operation(db, uid, body, parent, ApiError, recover=False):
    prior = db.execute('''SELECT o.signature,r.snapshot FROM source_sync_operations o
        JOIN source_sync_runs r ON r.user_id=o.user_id AND r.run_id=o.run_id
        WHERE o.user_id=? AND o.operation_id=?''',(uid,body['operation_id'])).fetchone()
    if prior:
        if prior['signature'] != operation_signature(parent,body.get('request_ids'),recover):
            raise ApiError(409, '同一操作编号不能用于不同运行或不同记录')
        return json.loads(prior['snapshot'])


def bind_operation(db, uid, body, parent, run_id, recover=False):
    db.execute('INSERT INTO source_sync_operations VALUES(?,?,?,?)',
               (uid,body['operation_id'],run_id,operation_signature(parent,body.get('request_ids'),recover)))


def start(db, uid, body, ApiError, *, trigger='manual', records=None, parent=None, recovery_targets=None):
    recover = recovery_targets is not None
    contract.check('recover' if recover else 'retry' if parent else 'start', body)
    prior = operation(db,uid,body,parent,ApiError,recover)
    if prior: return prior
    key = credential(db, uid)
    if not key: raise ApiError(400, '请先保存教学网账号和密码')
    active = db.execute("SELECT snapshot FROM source_sync_runs WHERE user_id=? AND state IN ('queued','crawling','recognizing')", (uid,)).fetchone()
    if active:
        if parent: raise ApiError(409, '已有同步正在执行，请等待或停止后重试')
        run = json.loads(active['snapshot'])
        bind_operation(db,uid,body,parent,run['run_id'])
        return run
    with _lock:
        if len(_workers)>=RUN_LIMIT: raise ApiError(503, '同步资源仍在使用或释放中，请稍后重试')
    if db.execute("SELECT count(*) FROM source_sync_runs WHERE state IN ('queued','crawling','recognizing')").fetchone()[0] >= RUN_LIMIT:
        raise ApiError(503, '同步队列已满，请稍后重试')
    run = dict(contract_version=contract.VERSION, run_id=secrets.token_hex(16), user_id=uid,
               operation_id=body['operation_id'], sources=['teaching_web'], trigger=trigger, parent_run_id=parent,
               state='queued', revision=0, created_at=stamp(), updated_at=stamp(), skipped=0,
               records=records or [], updated_tasks=[], counts={}, error=None)
    if parent: run['retry_selection'] = sorted(body['request_ids']) if 'request_ids' in body else None
    if recover: run.update(recover_sources=True,recovery_targets=recovery_targets,recovery_replacements={})
    db.execute('INSERT INTO source_sync_runs VALUES(?,?,?,?,?,?)', (uid, run['run_id'], body['operation_id'], key, 'queued', '{}'))
    bind_operation(db,uid,body,parent,run['run_id'],recover)
    save(db, run)
    return run


def record(request):
    row = request['records'][0]
    item = dict(request_id=request['request_id'], label=(row.get('text', '').split('\n')[0] or '课程通知')[:160],
                course=row.get('course_or_project'), state='queued', added=0, error=None, seconds=None)
    if row.get('input_id'): item['input_id'] = row['input_id']
    return item


def cancelled(path, uid, run_id, ApiError):
    with connection(path) as db:
        try: guard(db, uid, run_id, ApiError)
        except ApiError: return True
    return False


def auto_apply(db, uid, request_id, ApiError):
    batch = db.execute('SELECT request,demo FROM recognition_batches WHERE user_id=? AND request_id=?', (uid, request_id)).fetchone()
    request = json.loads(batch['request'])
    applied = set()
    for item in db.execute('SELECT * FROM recognition_candidates WHERE user_id=? AND request_id=? AND state="pending" AND version=1', (uid, request_id)).fetchall():
        candidate = json.loads(item['candidate'])
        if not recognition.can_auto_apply(candidate, request, batch['demo']): continue
        # Same submission/ownership/user-decision checks as the interactive endpoint.
        if teaching.submission.review_reason(teaching.submission.for_request(db, uid, request_id)): continue
        recognition.resolve_candidate(db, uid, item['id'], dict(action='create', auto_apply=True, version=1), ApiError)
        applied.add(item['id'])
    return added_items(db, uid, request_id, applied)


def process_record(path, uid, run_id, request_id, ApiError):
    started = time.monotonic()
    try:
        with connection(path) as db:
            db.execute('BEGIN IMMEDIATE'); guard(db, uid, run_id, ApiError)
            run = get(db, uid, run_id, ApiError)
            next(r for r in run['records'] if r['request_id']==request_id)['state'] = 'running'
            save(db, run)
        with cancel_if(lambda: cancelled(path, uid, run_id, ApiError)), connection(path) as db:
            teaching.run(db, uid, dict(contract_version=teaching.RUN, request_id=request_id), True, ApiError,
                         guard=lambda current: guard(current, uid, run_id, ApiError))
            if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
            guard(db, uid, run_id, ApiError)
            batch = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?', (uid, request_id)).fetchone()
            result = json.loads(batch['result'])
            run = get(db, uid, run_id, ApiError)
            item = next(r for r in run['records'] if r['request_id']==request_id)
            if teaching.needs_source_completion(result):
                item.update(state='incomplete', error=error('source_incomplete', '来源尚未完整取得，请重新获取来源或查看原文。'))
            elif result['status'] in ('success', 'no_tasks'):
                receipts = auto_apply(db, uid, request_id, ApiError)
                review = review_items(db, uid, request_id)
                item.update(state='succeeded', added=len(receipts), added_items=receipts,
                            review=len(review), review_items=review, error=None)
            else:
                failure = next((r.get('error') for r in result['record_results'] if r.get('error')), {})
                item.update(state='failed', error=error(failure.get('code', 'recognition_failed'), failure.get('message', '识别失败，请重试。'), failure.get('retryable', True)))
            item['seconds'] = round(time.monotonic()-started, 2)
            save(db, run)
    except Exception as exc:
        with connection(path) as db:
            db.execute('BEGIN IMMEDIATE')
            run = get(db, uid, run_id, ApiError)
            if run['state'] not in contract.ACTIVE: return
            item = next(r for r in run['records'] if r['request_id']==request_id)
            conflict = isinstance(exc, ApiError) and exc.status==409
            configuration = getattr(exc, 'code', None)=='model_configuration'
            item.update(state='failed', seconds=round(time.monotonic()-started, 2),
                        error=error('source_changed' if conflict else 'model_configuration' if configuration else 'recognition_failed',
                                    exc.message if isinstance(exc, ApiError) else '识别暂时失败，已保存的其他结果不受影响。', not (conflict or configuration)))
            save(db, run)


def collect_recovery(db, uid, run, ApiError, path):
    targets = run['recovery_targets']
    try:
        response = source_sync.collect(db, uid, ApiError, path.parent/'credential.key',
            guard=lambda current: guard(current, uid, run['run_id'], ApiError),
            input_ids={item['input_id'] for item in targets.values()})
    except Exception as exc:
        if db.in_transaction: db.rollback()
        if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
        guard(db, uid, run['run_id'], ApiError)
        run = get(db, uid, run['run_id'], ApiError)
        run['error'] = error('collection_failed', exc.message if isinstance(exc,ApiError) else '异常来源暂时无法重新获取，请稍后处理。', True)
        # A collection outage must not prevent recognition retries that can use
        # their already captured source. Incomplete sources remain unresolved.
        for item in run['records']:
            if targets[item['request_id']]['needs_collection']:
                item.update(state='incomplete',error=run['error'])
        return run
    guard(db, uid, run['run_id'], ApiError)
    run = get(db, uid, run['run_id'], ApiError)
    incoming = {request['records'][0]['input_id']: request for request in response['requests']}
    credentials = db.execute('SELECT username FROM source_credentials WHERE user_id=?',(uid,)).fetchone()
    records = []
    for original in run['records']:
        target = targets[original['request_id']]
        request = incoming.get(target['input_id'])
        if request:
            item = record(request)
            if item['request_id'] != original['request_id']:
                run['recovery_replacements'][item['request_id']] = original['request_id']
        else:
            item = original
            source = db.execute('SELECT request_id FROM teaching_sources WHERE user_id=? AND account=? AND input_id=?',
                (uid,teaching.account(credentials['username']),target['input_id'])).fetchone()
            batch = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?',
                (uid,source['request_id'])).fetchone() if source else None
            result = json.loads(batch['result']) if batch else None
            settled = result and result['status'] in ('success','no_tasks')
            if source and teaching.submission.skip_model(teaching.submission.for_request(db,uid,source['request_id'])): settled = True
            item.update(state='succeeded' if settled else 'incomplete',error=None if settled else
                error('source_incomplete' if result and teaching.needs_source_completion(result) else 'source_missing',
                      '重新获取后来源仍不完整，请稍后再处理或查看原文。' if result and teaching.needs_source_completion(result) else '本次未重新取得这条来源，请稍后再处理。'))
        records.append(item)
    run['records'] = records
    run['updated_tasks'] = response.get('updated_tasks', [])
    return run


def process(path, uid, run_id, ApiError):
    try:
        with _slots:
            with connection(path) as db:
                db.execute('BEGIN IMMEDIATE'); guard(db, uid, run_id, ApiError)
                run = get(db, uid, run_id, ApiError)
                save(db, run, 'recognizing' if run['parent_run_id'] and not run.get('recover_sources') else 'crawling')
            if run.get('recover_sources'):
                with connection(path) as db:
                    run = collect_recovery(db, uid, run, ApiError, path)
                    for item in run['records']:
                        if item['state'] == 'queued':
                            db.execute('INSERT INTO recognition_import_records VALUES(?,?,?,?,?,0) ON CONFLICT(user_id,request_id) DO UPDATE SET import_id=excluded.import_id,created_at=excluded.created_at,source=excluded.source,archived=0',
                                (uid,item['request_id'],run_id,run['created_at'],'教学网'))
                    save(db,run,'recognizing')
            elif not run['parent_run_id']:
                with connection(path) as db:
                    response = source_sync.collect(db, uid, ApiError, path.parent/'credential.key',
                                                   guard=lambda current: guard(current, uid, run_id, ApiError))
                    if len(response['requests']) > 1000: raise ApiError(413,'单轮待识别通知超过 1000 条，请缩小采集范围')
                    run = get(db, uid, run_id, ApiError)
                    run['records'] = [record(r) for r in response['requests']]
                    run['skipped'] = response['summary']['skipped']
                    run['updated_tasks'] = response.get('updated_tasks', [])
                    for item in run['records']:
                        db.execute('INSERT INTO recognition_import_records VALUES(?,?,?,?,?,0) ON CONFLICT(user_id,request_id) DO UPDATE SET import_id=excluded.import_id,created_at=excluded.created_at,source=excluded.source,archived=0',
                                   (uid, item['request_id'], run_id, run['created_at'], '教学网'))
                    save(db, run, 'recognizing')
            pending = iter(item for item in run['records'] if item['state']=='queued')
            with ThreadPoolExecutor(max_workers=CONCURRENCY, thread_name_prefix='source-record') as pool:
                futures = set()
                exhausted = False
                while not exhausted or futures:
                    if cancelled(path, uid, run_id, ApiError): break
                    while not exhausted and len(futures) < CONCURRENCY:
                        item = next(pending, None)
                        if item is None: exhausted=True;break
                        futures.add(pool.submit(process_record, path, uid, run_id, item['request_id'], ApiError))
                    if futures:
                        done, futures = wait(futures, timeout=.25, return_when=FIRST_COMPLETED)
                        for future in done: future.result()
            with connection(path) as db:
                db.execute('BEGIN IMMEDIATE')
                run = get(db, uid, run_id, ApiError)
                if run['state'] not in contract.ACTIVE: return
                try: guard(db,uid,run_id,ApiError)
                except ApiError as exc:
                    run['error'] = error('source_changed',exc.message,False)
                    for item in run['records']:
                        if item['state'] in ('queued','running'):
                            item.update(state='failed',error=run['error'])
                bad = bool(run.get('error')) or any(r['state'] != 'succeeded' for r in run['records'])
                good = any(r['state'] == 'succeeded' for r in run['records'])
                save(db, run, ('partial_success' if good else 'failed') if bad else 'succeeded')
    except Exception as exc:
        with connection(path) as db:
            db.execute('BEGIN IMMEDIATE')
            run = get(db, uid, run_id, ApiError)
            if run['state'] not in contract.ACTIVE: return
            run['error'] = error('collection_failed', exc.message if isinstance(exc, ApiError) else '同步暂时失败，请重新获取来源。', True)
            for item in run['records']:
                if item['state'] in ('queued','running'): item['state']='interrupted'
            save(db, run, 'failed')


def launch(path, uid, run_id, ApiError):
    key = (str(path), run_id)
    def work():
        try: process(path, uid, run_id, ApiError)
        finally:
            with _lock: _workers.pop(key, None)
    with _lock:
        if key in _workers: return
        thread = threading.Thread(target=work, daemon=True, name='source-sync')
        _workers[key] = thread; thread.start()


def wait_for_jobs(path, timeout=30):
    until = time.monotonic()+timeout
    while time.monotonic() < until:
        with _lock: workers = [t for (p,_),t in _workers.items() if p==str(path)]
        if not workers: return
        for worker in workers: worker.join(.05)
    raise TimeoutError('source sync workers still running')


def route(handler, db, uid, path, body, ApiError, database):
    root = '/api/sources/sync-runs'
    if not (path==root or path.startswith(root+'/')): return False
    if handler.command == 'GET': prune_quiet(db, uid)
    if path==root and handler.command=='POST':
        db.execute('BEGIN IMMEDIATE')
        prune_quiet(db, uid)
        run = start(db, uid, body, ApiError)
        db.commit()
        if run['state'] in contract.ACTIVE: launch(database, uid, run['run_id'], ApiError)
        handler.reply(202, view(run)); return True
    if path==root and handler.command=='GET':
        rows = db.execute('''SELECT r.snapshot FROM source_sync_runs r LEFT JOIN source_sync_quiet_runs q ON r.run_id=q.run_id
            WHERE r.user_id=? AND q.run_id IS NULL ORDER BY r.rowid DESC LIMIT 20''', (uid,)).fetchall()
        last = db.execute('SELECT snapshot FROM source_sync_checks WHERE user_id=?', (uid,)).fetchone()
        handler.reply(200, dict(contract_version=contract.VERSION, runs=[view(json.loads(r['snapshot'])) for r in rows],
                                last_check=json.loads(last['snapshot']) if last else None)); return True
    run_id, *suffix = path[len(root)+1:].split('/')
    if handler.command=='GET' and not suffix:
        handler.reply(200, view(get(db, uid, run_id, ApiError))); return True
    if handler.command=='POST' and suffix==['stop']:
        contract.check('stop',body); db.execute('BEGIN IMMEDIATE')
        prune_quiet(db, uid)
        run = get(db, uid, run_id, ApiError)
        stop(db,run)
        handler.reply(200,view(run)); return True
    if handler.command=='POST' and suffix in (['retry'],['recover']):
        recover = suffix == ['recover']
        contract.check('recover' if recover else 'retry',body); db.execute('BEGIN IMMEDIATE')
        prune_quiet(db, uid)
        old = get(db,uid,run_id,ApiError)
        # A lost HTTP response can be retried even after its child has completed.
        prior = operation(db,uid,body,run_id,ApiError,recover)
        if prior:
            run = start(db,uid,body,ApiError,trigger='retry',parent=run_id,recovery_targets={} if recover else None)
            db.commit()
            if run['state'] in contract.ACTIVE: launch(database,uid,run['run_id'],ApiError)
            handler.reply(202,view(run));return True
        if old['state'] in contract.ACTIVE: raise ApiError(409,'本轮同步仍在执行，请等待或停止后继续')
        key = db.execute('SELECT credential FROM source_sync_runs WHERE user_id=? AND run_id=?',(uid,run_id)).fetchone()[0]
        if credential(db,uid)!=key: raise ApiError(409,'教学网凭据已改变，请重新获取来源')
        if recover: records,targets = recovery_records(db,uid,old,body.get('request_ids'),ApiError)
        else: records,targets = retry_records(db,uid,old,body.get('request_ids'),ApiError),None
        run=start(db,uid,body,ApiError,trigger='retry',records=records,parent=run_id,recovery_targets=targets)
        db.commit()
        if run['state'] in contract.ACTIVE: launch(database,uid,run['run_id'],ApiError)
        handler.reply(202,view(run));return True
    raise ApiError(404,'同步操作不存在')
