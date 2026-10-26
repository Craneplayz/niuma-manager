"""Authenticated teaching snapshots -> v0.4 recognition -> existing workspace cards."""
from copy import deepcopy
import hashlib
import json
if __package__:
    from .runtime_environment import model_env_path
else:
    from runtime_environment import model_env_path
from pathlib import Path
import secrets
import teaching_submission as submission
import teaching_assets
import teaching_identity
from server.model_queue import WAIT_ERROR, disconnected
from server import operations
import time
from server import recognition_diagnostics as diagnostics
from server import course_context

from contracts.teaching_io import canonical, check_request, snapshot_digest
from contracts.teaching_capture import check_capture
from ddl_text.chat_client import ChatClient
from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.teaching_draft import CLASSIFICATION_MODE, UNRESOLVED_TIME
from ddl_text.teaching_pipeline import process_teaching_request, validate_teaching_result
from ddl_text.teaching_policy import teaching_policy_manifest

ROOT = Path(__file__).resolve().parents[1]
VIEW = 'teaching-workspace/0.1'
RUN = 'teaching-run/0.1'
SOURCE_INCOMPLETE = '来源待补全：已读部分未发现任务，但正文或附件未完整读取。重试识别不会补抓内容，请查看来源或补全后重新同步。'


def needs_source_completion(result):
    rows = result['record_results']
    return any(row['status']=='incomplete' for row in rows) and not any(row['status']=='failed' for row in rows)


def initialize(db):
    diagnostics.initialize(db)
    submission.initialize(db)
    teaching_assets.initialize(db)
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_sources(
        user_id TEXT NOT NULL, account TEXT NOT NULL, input_id TEXT NOT NULL,
        digest TEXT NOT NULL, request_id TEXT NOT NULL, request TEXT NOT NULL,
        PRIMARY KEY(user_id,account,input_id))''')
    # Snapshot lookup and attachment pruning use request_id, not input_id.
    db.execute('''CREATE INDEX IF NOT EXISTS teaching_sources_request
        ON teaching_sources(user_id,account,request_id)''')
    course_context.initialize(db)
    teaching_identity.upgrade_pending_candidates(db)


def account(username):
    return hashlib.sha256(username.encode()).hexdigest()


def request_view(request):
    records = []
    for entry in request['records']:
        r, source = entry['record'], entry['source']
        p = r['published_at']
        records.append(dict(input_id=r['input_id'], platform=r['platform'], text=r['text'],
            text_quality=r['body_status'], published_at=dict(value=p['value'] if p else None,
                precision=('datetime' if 'T' in p['value'] else 'date') if p else 'unknown',
                basis=p['basis'] if p else 'unknown'), collected_at=r['collected_at'],
            timezone=r['timezone'], timezone_basis='source', source_url=source['url'],
            source_label=source['label'], source_item_id=r['source_item_id'],
            course_or_project=source['course_or_project'], context=[],
            upstream_hints=dict(references=r['references'], course_term=r['course_term'],
                                **({'course_context':r['course_context']} if 'course_context' in r else {}))))
    return dict(contract_version=VIEW, request_id=request['request_id'], records=records)


def candidate_view(decision, entry, *, model_routing=False):
    r, source = entry['record'], entry['source']
    a = decision['pair']['assessment']; d = a['deadline']
    # A guessed year can be shown without granting auto-apply. Keep actual
    # unresolved/contradictory dates hidden, but do not erase a valid proposal
    # merely because the source's age still needs confirmation.
    codes = set(decision['review_codes'])
    known = d['value'] is not None and (model_routing or not (UNRESOLVED_TIME - {'original_age_unknown'}).intersection(codes))
    context={d['ref_id']:d for d in r.get('course_context',{}).get('documents',[])}
    inferred = known and ((not model_routing and 'original_age_unknown' in codes)
                          or d['derivation'] == 'course_rule'
                          or any(q['part_id'] in context for q in d['evidence']))
    evidence = []; by_quote = {}
    sources=[dict(input_id=r['input_id'],platform=r['platform'],url=source['url'],label=source['label'],source_item_id=r['source_item_id'])]
    for quotes, supports in [(a['action']['evidence'], ['title','description']), (d['evidence'], ['deadline'])]:
        for q in quotes:
            key=(q['part_id'],q['quote'])
            if key not in by_quote:
                origin=context.get(q['part_id'])
                by_quote[key]=dict(input_id=origin['input_id'] if origin else r['input_id'],
                    field=origin['part_id'] if origin else q['part_id'],quote=q['quote'],supports=[])
                evidence.append(by_quote[key])
                if origin and not any(s['input_id']==origin['input_id'] for s in sources):
                    sources.append(dict(input_id=origin['input_id'],platform=r['platform'],url=origin['source_url'],
                                        label=origin['source_label'],source_item_id=None))
            by_quote[key]['supports']=list(dict.fromkeys(by_quote[key]['supports']+supports))
    return dict(candidate_id=a['candidate_id'], input_id=r['input_id'], title=decision['title'],
        content_type=decision.get('content_type', 'task'),
        description=decision['description'], course_or_project=source['course_or_project'],
        course_basis='provided' if source['course_or_project'] else 'unknown',
        notice_type=decision['notice_type'], target_reference=decision['target']['quote'] if decision['target'] else None, prerequisites=[],
        deadline=dict(kind='deadline', status='inferred' if inferred else 'explicit' if known else 'not_provided' if d['derivation']=='not_provided' else 'unknown',
            value=d['value'] if known else None, precision=d['precision'] if known else 'unknown',
            timezone=r['timezone'], alternatives=[],
            raw=d['evidence'][0]['quote'] if d['evidence'] else None,
            basis='依据原文与课程时间信息推定日期；具体依据见证据。' if inferred else '教学网正文及课程上下文' if known else '原文未提供截止要求。' if d['derivation']=='not_provided' else '请查看原文中的截止要求。'),
        needs_review=decision['recommendation']!='task', review_reasons=decision['review_reasons'],
        evidence=evidence, sources=sources)


def result_view(request, result):
    tasks, rows, errors = [], [], []
    model_routing = result.get('classification_mode') == CLASSIFICATION_MODE
    for entry, row in zip(request['records'], result['record_results']):
        # Viewing a stored result must not reclassify it or change user decisions.
        decisions=row['decisions']
        cards = [candidate_view(d, entry, model_routing=model_routing) for d in decisions if d['recommendation']!='filtered']
        tasks.extend(cards)
        warnings = list(row['warnings'])
        warnings.extend('history_filtered:'+str(d['filter_reason']) for d in decisions if d['recommendation']=='filtered')
        if row['status']=='no_tasks': warnings.append('no_action:未发现行动要求')
        failed = row['status'] in ('failed','incomplete')
        rows.append(dict(input_id=row['input_id'], status='failed' if failed else 'success' if cards else 'no_tasks',
            task_ids=[c['candidate_id'] for c in cards], warnings=warnings))
        if failed:
            failure = row['error'] or dict(code='incomplete_source',message=SOURCE_INCOMPLETE,retryable=False)
            errors.append(dict(input_id=row['input_id'], **{k:failure[k] for k in
                ('code','message','retryable','failure','diagnostic','diagnostic_id') if k in failure}))
    return dict(contract_version=VIEW, request_id=request['request_id'], status=result['status'],
        record_results=rows, tasks=tasks, errors=errors)


def register(db, uid, username, requests, *, assets=None):
    """Only call for the authenticated crawler output, never for browser JSON."""
    from recognition import has_unapplied_auto_tasks
    pending, seen = [], set(); total = skipped = 0
    if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
    for batch in requests:
        assignment = None
        captured_state = batch.get('contract_version')=='teaching-capture/0.1'
        if captured_state:
            check_capture(batch)
            assignment = batch['assignment']; batch = batch['request']
        else: check_request(batch)
        for entry in batch['records']:
            entry = deepcopy(entry); r = entry['record']
            if 'course_context' in r or any(ref['kind']=='course_context' for ref in r['references']):
                raise ValueError('course context is selected by the server, not the capture producer')
            if r['input_id'] in seen: continue
            seen.add(r['input_id']); total += 1
            previous_state = submission.source_state(db,uid,account(username),r['input_id'])
            observation = assignment if assignment is not None else {'state':'unknown'} if captured_state else None
            if submission.stale(previous_state,r,observation):
                skipped += 1
                continue
            files = (assets or {}).get(r['input_id'],[])
            digest = teaching_identity.source_digest(entry, assignment, files)
            row = db.execute('SELECT * FROM teaching_sources WHERE user_id=? AND account=? AND input_id=?',
                (uid, account(username), r['input_id'])).fetchone()
            # Upgrade old fingerprints using their original capture and bytes,
            # without needlessly re-running successful recognition on deployment.
            previous_digest = None
            if row and row['digest'] != digest:
                old = json.loads(row['request'])['records'][0]
                old_files = teaching_assets.load(db,uid,account(username),row['request_id'])
                legacy = deepcopy(old)
                for key in ('collected_at','snapshot_id'): legacy['record'].pop(key)
                legacy_material = dict(entry=legacy,assignment=assignment) if assignment else legacy
                if old_files: legacy_material = dict(source=legacy_material,assets=teaching_assets.identity(old_files))
                if row['digest']==hashlib.sha256(canonical(legacy_material)).hexdigest():
                    previous_digest = teaching_identity.source_digest(old, assignment, old_files)
            if row and (row['digest']==digest or previous_digest==digest):
                request = json.loads(row['request'])
                db.execute('UPDATE teaching_sources SET digest=? WHERE user_id=? AND account=? AND input_id=?',
                           (digest,uid,account(username),r['input_id']))
            else:
                r['snapshot_id'] = 'snapshot:'+secrets.token_hex(16)
                request = dict(contract_version='0.4', request_id='teaching:'+secrets.token_hex(16), records=[entry])
                db.execute('INSERT INTO teaching_sources VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,account,input_id) DO UPDATE SET digest=excluded.digest,request_id=excluded.request_id,request=excluded.request',
                    (uid, account(username), r['input_id'], digest, request['request_id'], json.dumps(request)))
            # Store retains unchanged files and frees retired files before quota
            # checks, atomically with the source revision above.
            attempted = teaching_assets.attempted_identity(db,uid,account(username),request['request_id'])
            retained = attempted if attempted is not None else teaching_assets.retained_identity(db,uid,account(username),request['request_id'])
            teaching_assets.store(db,uid,account(username),request['request_id'],files)
            if (row and request['request_id']==row['request_id']
                    and teaching_assets.retained_identity(db,uid,account(username),request['request_id']) - retained):
                # Captured bytes were known but not retained (e.g. quota). Their
                # arrival is new recognition input even though the source digest
                # is unchanged. Advance the snapshot, preserving prior results
                # and decisions; late attempts on the old snapshot must not win.
                old_request_id=request['request_id']
                request=deepcopy(request)
                request['request_id']='teaching:'+secrets.token_hex(16)
                request['records'][0]['record']['snapshot_id']='snapshot:'+secrets.token_hex(16)
                db.execute('UPDATE teaching_sources SET request_id=?,request=? WHERE user_id=? AND account=? AND input_id=?',
                    (request['request_id'],json.dumps(request),uid,account(username),r['input_id']))
                teaching_assets.rebind(db,uid,account(username),old_request_id,request['request_id'])
            state = submission.observe(db,uid,account(username),r,request['request_id'],observation,
                                       old_request_id=row['request_id'] if row else None,source=entry['source'],
                                       assets=teaching_assets.identity(files))
            course_context.index(db,uid,account(username),request['records'][0]['record'])
            if submission.skip_model(state):
                skipped += 1
                continue
            stored = db.execute('SELECT result,demo FROM recognition_batches WHERE user_id=? AND request_id=?',
                (uid,request['request_id'])).fetchone()
            output = json.loads(stored['result']) if stored else None
            if output and (needs_source_completion(output) or output['status'] in ('success','no_tasks')
                           and not has_unapplied_auto_tasks(db,uid,request,stored['demo'])):
                skipped += 1
            else: pending.append(request_view(request))
    for old,current in course_context.refresh_dependents(db,uid,account(username)):
        pending=[p for p in pending if p['request_id']!=old]
        state=submission.source_state(db,uid,account(username),current['records'][0]['record']['input_id'])
        if not submission.skip_model(state): pending.append(request_view(current))
    # Dependencies are captured when recognition actually starts, after every
    # source in this sync has been registered; capture order is irrelevant.
    return dict(requests=pending, summary=dict(total=max(total,skipped+len(pending)), skipped=skipped, pending=len(pending)))


def resolve_snapshot(db, uid, request_id, ApiError):
    cred = db.execute('SELECT username FROM source_credentials WHERE user_id=?',(uid,)).fetchone()
    row = db.execute('SELECT request FROM teaching_sources WHERE user_id=? AND account=? AND request_id=?',
        (uid, account(cred['username']), request_id)).fetchone() if cred else None
    if not row: raise ApiError(409,'教学网来源已变化或凭据已移除，请重新同步')
    submission.guard_model(db,uid,request_id,ApiError)
    return json.loads(row['request'])


def recognize(request, ApiError, *, files=(), save_cache=None, prepare_context=None):
    try:
        options = live_options(model_env_path(ROOT))
        if not options['key'].strip() or options['provider']!='qiniu': raise ValueError()
        # Qiniu's DeepSeek defaults to thinking; it can exhaust all 8192 tokens
        # before producing JSON. Keep the bounded extraction call non-thinking.
        settings = Settings(mode='real', model=options['model'], endpoint=options['endpoint'], timeout_seconds=90, max_retries=0,
                            thinking_mode='disabled' if options['model'].startswith('deepseek/') else None)
        policy = teaching_policy_manifest(settings)
    except (OSError,ValueError,KeyError,TypeError):
        error = ApiError(503,'教学网识别配置不可用，请检查当前环境的七牛云模型配置并重启后端')
        error.code = 'model_configuration'
        raise error from None
    client = operations.observe_chat(ChatClient(options['key']))
    diagnostics.event('configuration',model=settings.model,timeout_seconds=settings.timeout_seconds)
    def check_active():
        if disconnected(): raise ApiError(409,'识别已停止或来源已变化，已读附件进度保留。')
    teaching_assets.enrich(request,files,client,settings,save_cache,guard=check_active)
    if prepare_context: prepare_context(request,client,settings,check_active)
    check_active()
    with operations.span('recognition') as observation:
        result = process_teaching_request(request, client=client, settings=settings,
            verified_snapshot_digests={snapshot_digest(e) for e in request['records']},
            budget_seconds=90,diagnostic_event=diagnostics.event)
        if result['status'] in ('failed','partial_success'):
            code=next((row['error']['code'] for row in result['record_results'] if row.get('error')),'upstream_error')
            observation.update(result='failed',code=code if code in operations.CODES else 'upstream_error')
    return result, policy


def attachment_context(db, uid, request_id):
    """Resolve receipts under the current source account; cache writes use their own short connection."""
    import sqlite3
    from contextlib import closing
    cred = db.execute('SELECT username FROM source_credentials WHERE user_id=?',(uid,)).fetchone()
    files = teaching_assets.load(db,uid,account(cred['username']),request_id) if cred else []
    path = db.execute('PRAGMA database_list').fetchone()[2]
    def save_cache(asset,version,result):
        if not path: return  # In-memory tests need no durable cache.
        with closing(sqlite3.connect(path,timeout=30,factory=operations.TimedConnection)) as cache_db, cache_db:
            cache_db.execute('BEGIN IMMEDIATE')
            teaching_assets.save_read_cache(cache_db,uid,asset,version,result)
    if not cred: return {}
    acct=account(cred['username'])
    row=db.execute('SELECT request FROM teaching_sources WHERE user_id=? AND account=? AND request_id=?',
                   (uid,acct,request_id)).fetchone()
    if not row: return dict(files=files,save_cache=save_cache) if files else {}
    captured=json.loads(row[0]); selected=course_context.select(db,uid,acct,captured['records'][0])
    def load_files(item):
        if not path: return teaching_assets.load(db,uid,acct,item['request']['request_id'])
        with closing(sqlite3.connect(path,timeout=30)) as current_db:
            current_db.row_factory=sqlite3.Row
            row=current_db.execute('SELECT request_id,digest FROM teaching_sources WHERE user_id=? AND account=? AND input_id=?',
                                   (uid,acct,item['request']['records'][0]['record']['input_id'])).fetchone()
            return teaching_assets.load(current_db,uid,acct,row['request_id']) if row and row['digest']==item['digest'] else []
    def prepare_context(request,client,settings,guard):
        def read_token(item,references):
            options=dict(references=references,observed=item.get('observed_receipts',()))
            if not path: return course_context.readiness(db,uid,acct,item['request']['request_id'],**options)
            with closing(sqlite3.connect(path,timeout=30)) as current_db:
                return course_context.readiness(current_db,uid,acct,item['request']['request_id'],**options)
        def load_cached(item,version):
            if not path: return teaching_assets.load_cached(db,uid,acct,item['request']['request_id'],version)
            with closing(sqlite3.connect(path,timeout=30)) as current_db:
                return teaching_assets.load_cached(current_db,uid,acct,item['request']['request_id'],version)
        course_context.prepare(request,selected,load_files,client,settings,save_cache,guard,read_token=read_token,load_cached=load_cached)
    prepare_context.selected=selected
    # Preserve compatibility for callers without a captured course/semester.
    if not selected:
        return dict(files=files,save_cache=save_cache) if files else {}
    return dict(files=files,save_cache=save_cache,prepare_context=prepare_context)


def validate_current_result(request, result, policy, ApiError):
    try:
        if result.get('classification_mode') != CLASSIFICATION_MODE: raise ValueError()
        validate_teaching_result(request,result,verified_snapshot_digests={snapshot_digest(e) for e in request['records']})
        if not policy or result['policy_version']!=policy['version']: raise ValueError()
    except Exception as cause:
        from ddl_text import diagnostics as trace
        trace.failure(cause,stage='result_validation',reason='result_contract')
        error=ApiError(502,'教学网结果未通过证据和版本校验，未保存候选')
        error.code='invalid_agent_output'
        error.diagnostic='result_contract'
        raise error from None


def run(db, uid, body, retry, ApiError, *, guard=None, diagnostic_attempt=None):
    import recognition
    if set(body)!={'contract_version','request_id'} or body['contract_version']!=RUN or not isinstance(body['request_id'],str) or len(body['request_id'])>256:
        raise ApiError(400,'教学网运行请求仅接受服务器返回的 request_id')
    if guard: guard(db)
    request = resolve_snapshot(db,uid,body['request_id'],ApiError)
    captured = deepcopy(request)
    control_token = submission.controls.token(db,uid,request['request_id'])
    cred=db.execute('SELECT username FROM source_credentials WHERE user_id=?',(uid,)).fetchone()
    acct=account(cred['username'])
    context_revision=course_context.catalog_revision(db,uid,acct,captured['records'][0])
    attachments = attachment_context(db,uid,request['request_id'])
    context_selection=getattr(attachments.get('prepare_context'),'selected',None)
    if context_selection is None: context_selection=course_context.select(db,uid,acct,captured['records'][0])
    previous = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,request['request_id'])).fetchone()
    if previous and (not retry or json.loads(previous['result'])['status'] in ('success','no_tasks')):
        operations.emit('outcome',code='cache_hit')
        return
    if previous:operations.emit('outcome',code='retry')
    if previous and needs_source_completion(json.loads(previous['result'])):
        raise ApiError(409,SOURCE_INCOMPLETE)
    if not recognition._ACTIVE.acquire():
        with diagnostics.capture(db,uid,request['request_id']):
            error=ApiError(503,WAIT_ERROR)
            error.code,error.diagnostic='agent_unavailable','model_busy'
            raise error
    try:
        if guard: guard(db)
        with diagnostics.capture(db,uid,request['request_id']) as attempt:
            if diagnostic_attempt is not None and attempt is not None:
                diagnostic_attempt['id'] = attempt['attempt_id']
            result, policy = recognize(request,ApiError,**attachments)
            diagnostics.stamp(result)
            for outcome in result['record_results']:
                failure = outcome.get('error') or {}
                diagnostics.event('result',status=outcome['status'],code=failure.get('code'),schema=failure.get('diagnostic'))
            with operations.span('validation'):validate_current_result(request,result,policy,ApiError)
            saving=time.monotonic()
            db.execute('BEGIN IMMEDIATE')
            if guard: guard(db)
            if resolve_snapshot(db,uid,request['request_id'],ApiError)!=captured: raise ApiError(409,'来源在识别期间发生变化，请重新同步')
            if (not course_context.current(db,uid,acct,context_selection)
                    or course_context.catalog_revision(db,uid,acct,captured['records'][0])!=context_revision):
                raise ApiError(409,'课程资料在识别期间发生变化，请重新同步')
            if submission.controls.token(db,uid,request['request_id'])!=control_token: raise ApiError(409,'作业关联在识别期间发生变化，请重新同步')
            now = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,request['request_id'])).fetchone()
            if now and (not previous or now['result']!=previous['result']):
                # The stored result belongs to the competing attempt, not this one.
                # Without its exact ID, omit diagnostics rather than misattribute it.
                if diagnostic_attempt is not None: diagnostic_attempt.clear()
                return
            # Every stored teaching request is a single record. Failed/incomplete attempts have no cards.
            attempt_id = attempt['attempt_id'] if attempt is not None else None
            if previous:
                db.execute('UPDATE recognition_batches SET result=?,request=?,fingerprint=?,attempt_id=? WHERE user_id=? AND request_id=?',
                    (json.dumps(result),json.dumps(request),hashlib.sha256(canonical(request)).hexdigest(),attempt_id,uid,request['request_id']))
            else:
                db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,0,?)',(uid,request['request_id'],hashlib.sha256(canonical(request)).hexdigest(),json.dumps(request),json.dumps(result),attempt_id))
            cred=db.execute('SELECT username FROM source_credentials WHERE user_id=?',(uid,)).fetchone()
            teaching_assets.remember_inputs(db,uid,account(cred['username']),request['request_id'],attachments.get('files',[]))
            course_context.remember(db,uid,acct,captured['records'][0],context_selection,
                references=request['records'][0]['record']['references'],files=attachments.get('files',[]))
            unchanged = teaching_identity.reuse_unchanged_result(db,uid,request['request_id'],result)
            if result['status'] in ('success','no_tasks') and not unchanged:
                teaching_identity.supersede_unhandled(db,uid,request['request_id'],captured['records'][0]['record']['snapshot_id'])
            cards = [] if unchanged else teaching_identity.protect_source_changes(db,uid,request['request_id'],result_view(request,result)['tasks'])
            for card in cards:
                db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',
                    (secrets.token_hex(16),uid,request['request_id'],json.dumps(card)))
            submission.bind_recognition(db,uid,captured,result,teaching_assets.identity(attachments.get('files',[])))
            for saved in db.execute("SELECT id,candidate FROM recognition_candidates WHERE user_id=? AND request_id=? AND state='pending'",
                                    (uid,request['request_id'])).fetchall():
                card = submission.decorate(db,uid,request['request_id'],json.loads(saved['candidate']))
                db.execute('UPDATE recognition_candidates SET candidate=? WHERE id=?',(json.dumps(card),saved['id']))
            db.execute('INSERT INTO recognition_record_policies VALUES(?,?,?,?) ON CONFLICT(user_id,request_id,input_id) DO UPDATE SET policy=excluded.policy',
                (uid,request['request_id'],request['records'][0]['record']['input_id'],json.dumps(policy)))
            submission.controls.refresh_request(db,uid,request['request_id'])
            operations.emit('save',(time.monotonic()-saving)*1000)
            for row in result['record_results']:
                code=(row.get('error') or {}).get('code') or ('succeeded' if row['status']=='success' else row['status'])
                if code in operations.CODES:operations.after_commit(db,'outcome',code=code,result='failed' if row['status']=='failed' else 'ok')
                filtered=sum(decision.get('recommendation')=='filtered' for decision in row.get('decisions',[]))
                if filtered:operations.after_commit(db,'outcome',code='filtered',quantity=filtered)
    finally:
        recognition._ACTIVE.release()
