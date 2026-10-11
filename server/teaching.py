"""Authenticated teaching snapshots -> v0.4 recognition -> existing workspace cards."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import secrets
import teaching_submission as submission

from contracts.teaching_io import canonical, check_request, snapshot_digest
from contracts.teaching_capture import check_capture
from ddl_text.chat_client import ChatClient
from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.teaching_draft import UNRESOLVED_TIME, compile_teaching_draft
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
    submission.initialize(db)
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_sources(
        user_id TEXT NOT NULL, account TEXT NOT NULL, input_id TEXT NOT NULL,
        digest TEXT NOT NULL, request_id TEXT NOT NULL, request TEXT NOT NULL,
        PRIMARY KEY(user_id,account,input_id))''')


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
            upstream_hints=dict(references=r['references'], course_term=r['course_term'])))
    return dict(contract_version=VIEW, request_id=request['request_id'], records=records)


def candidate_view(decision, entry):
    r, source = entry['record'], entry['source']
    a = decision['pair']['assessment']; d = a['deadline']
    # A guessed year can be shown without granting auto-apply. Keep actual
    # unresolved/contradictory dates hidden, but do not erase a valid proposal
    # merely because the source's age still needs confirmation.
    codes = set(decision['review_codes'])
    known = d['value'] is not None and not (UNRESOLVED_TIME - {'original_age_unknown'}).intersection(codes)
    inferred = known and 'original_age_unknown' in codes
    evidence = []; by_quote = {}
    for quotes, supports in [(a['action']['evidence'], ['title','description']), (d['evidence'], ['deadline'])]:
        for q in quotes:
            key=(q['part_id'],q['quote'])
            if key not in by_quote:
                by_quote[key]=dict(input_id=r['input_id'],field=q['part_id'],quote=q['quote'],supports=[])
                evidence.append(by_quote[key])
            by_quote[key]['supports']=list(dict.fromkeys(by_quote[key]['supports']+supports))
    return dict(candidate_id=a['candidate_id'], input_id=r['input_id'], title=decision['title'],
        description=decision['description'], course_or_project=source['course_or_project'],
        course_basis='provided' if source['course_or_project'] else 'unknown',
        notice_type=decision['notice_type'], target_reference=decision['target']['quote'] if decision['target'] else None, prerequisites=[],
        deadline=dict(kind='deadline', status='inferred' if inferred else 'explicit' if known else 'not_provided' if d['derivation']=='not_provided' else 'unknown',
            value=d['value'] if known else None, precision=d['precision'] if known else 'unknown',
            timezone=r['timezone'], alternatives=[],
            raw=d['evidence'][0]['quote'] if d['evidence'] else None,
            basis='日期已识别，来源年代待核实。' if inferred else '教学网正文及课程上下文' if known else '请查看原文中的截止要求。'),
        needs_review=decision['recommendation']!='task', review_reasons=decision['review_reasons'],
        evidence=evidence, sources=[dict(input_id=r['input_id'], platform=r['platform'],
            url=source['url'], label=source['label'], source_item_id=r['source_item_id'])])


def history_view(decision):
    """Only demote old pending history; never promote a saved model run to a task."""
    if decision['recommendation']!='candidate' or decision['review_codes']:
        return decision
    draft={key:deepcopy(decision[key]) for key in ('title','description','notice_type','target','relevance')}
    draft['assessment']=deepcopy(decision['pair']['assessment'])
    for key in ('input_id','candidate_id'):draft['assessment'].pop(key)
    try:
        # A saved decision without source_unverified passed the authenticated
        # source gate. Recheck its unchanged evidence; no new model call/version.
        refreshed=compile_teaching_draft(json.dumps(dict(tasks=[draft]),ensure_ascii=False),
            decision['pair']['record'],source_verified=True)[0]
    except Exception:
        return decision
    if refreshed['recommendation']!='filtered':return decision
    return dict(decision,recommendation='filtered',review_codes=[],review_reasons=[],filter_reason=refreshed['filter_reason'])


def result_view(request, result):
    tasks, rows, errors = [], [], []
    for entry, row in zip(request['records'], result['record_results']):
        decisions=[history_view(d) for d in row['decisions']]
        cards = [candidate_view(d, entry) for d in decisions if d['recommendation']!='filtered']
        tasks.extend(cards)
        warnings = list(row['warnings'])
        warnings.extend('history_filtered:'+str(d['filter_reason']) for d in decisions if d['recommendation']=='filtered')
        if row['status']=='no_tasks': warnings.append('no_action:未发现行动要求')
        failed = row['status'] in ('failed','incomplete')
        rows.append(dict(input_id=row['input_id'], status='failed' if failed else 'success' if cards else 'no_tasks',
            task_ids=[c['candidate_id'] for c in cards], warnings=warnings))
        if failed:
            errors.append(dict(input_id=row['input_id'], **(row['error'] or dict(code='incomplete_source',
                message=SOURCE_INCOMPLETE, retryable=False))))
    return dict(contract_version=VIEW, request_id=request['request_id'], status=result['status'],
        record_results=rows, tasks=tasks, errors=errors)


def register(db, uid, username, requests):
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
            if r['input_id'] in seen: continue
            seen.add(r['input_id']); total += 1
            previous_state = submission.source_state(db,uid,account(username),r['input_id'])
            observation = assignment if assignment is not None else {'state':'unknown'} if captured_state else None
            if submission.stale(previous_state,r,observation):
                skipped += 1
                continue
            stable = deepcopy(entry)
            for key in ('collected_at','snapshot_id'): stable['record'].pop(key)
            digest = hashlib.sha256(canonical(dict(entry=stable,assignment=assignment) if assignment else stable)).hexdigest()
            row = db.execute('SELECT * FROM teaching_sources WHERE user_id=? AND account=? AND input_id=?',
                (uid, account(username), r['input_id'])).fetchone()
            if row and row['digest']==digest:
                request = json.loads(row['request'])
            else:
                r['snapshot_id'] = 'snapshot:'+secrets.token_hex(16)
                request = dict(contract_version='0.4', request_id='teaching:'+secrets.token_hex(16), records=[entry])
                db.execute('INSERT INTO teaching_sources VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,account,input_id) DO UPDATE SET digest=excluded.digest,request_id=excluded.request_id,request=excluded.request',
                    (uid, account(username), r['input_id'], digest, request['request_id'], json.dumps(request)))
            state = submission.observe(db,uid,account(username),r,request['request_id'],observation,
                                       old_request_id=row['request_id'] if row else None)
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
    return dict(requests=pending, summary=dict(total=total, skipped=skipped, pending=len(pending)))


def resolve_snapshot(db, uid, request_id, ApiError):
    cred = db.execute('SELECT username FROM source_credentials WHERE user_id=?',(uid,)).fetchone()
    row = db.execute('SELECT request FROM teaching_sources WHERE user_id=? AND account=? AND request_id=?',
        (uid, account(cred['username']), request_id)).fetchone() if cred else None
    if not row: raise ApiError(409,'教学网来源已变化或凭据已移除，请重新同步')
    submission.guard_model(db,uid,request_id,ApiError)
    return json.loads(row['request'])


def recognize(request, ApiError):
    try:
        options = live_options(ROOT/'.env')
        if not options['key'].strip() or options['provider']!='qiniu': raise ValueError()
        # Qiniu's DeepSeek defaults to thinking; it can exhaust all 8192 tokens
        # before producing JSON. Keep the bounded extraction call non-thinking.
        settings = Settings(mode='real', model=options['model'], endpoint=options['endpoint'], timeout_seconds=90, max_retries=0,
                            thinking_mode='disabled' if options['model'].startswith('deepseek/') else None)
        policy = teaching_policy_manifest(settings)
    except (OSError,ValueError,KeyError,TypeError):
        raise ApiError(503,'教学网识别配置不可用，请检查 .env 中七牛云模型配置并重启后端') from None
    result = process_teaching_request(request, client=ChatClient(options['key']), settings=settings,
        verified_snapshot_digests={snapshot_digest(e) for e in request['records']}, budget_seconds=90)
    return result, policy


def run(db, uid, body, retry, ApiError):
    import recognition
    if set(body)!={'contract_version','request_id'} or body['contract_version']!=RUN or not isinstance(body['request_id'],str) or len(body['request_id'])>256:
        raise ApiError(400,'教学网运行请求仅接受服务器返回的 request_id')
    request = resolve_snapshot(db,uid,body['request_id'],ApiError)
    previous = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,request['request_id'])).fetchone()
    if previous and (not retry or json.loads(previous['result'])['status'] in ('success','no_tasks')): return
    if previous and needs_source_completion(json.loads(previous['result'])):
        raise ApiError(409,SOURCE_INCOMPLETE)
    if not recognition._ACTIVE.acquire(blocking=False): raise ApiError(503,'识别正在处理其他请求，请稍后重试')
    try:
        result, policy = recognize(request,ApiError)
        try:
            validate_teaching_result(request,result,verified_snapshot_digests={snapshot_digest(e) for e in request['records']})
            if not policy or result['policy_version']!=policy['version']: raise ValueError()
        except Exception:
            raise ApiError(502,'教学网结果未通过证据和版本校验，未保存候选') from None
        db.execute('BEGIN IMMEDIATE')
        if resolve_snapshot(db,uid,request['request_id'],ApiError)!=request: raise ApiError(409,'来源在识别期间发生变化，请重新同步')
        now = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,request['request_id'])).fetchone()
        if now and (not previous or now['result']!=previous['result']): return
        # Every stored teaching request is a single record. Failed/incomplete attempts have no cards.
        if previous:
            db.execute('UPDATE recognition_batches SET result=? WHERE user_id=? AND request_id=?',(json.dumps(result),uid,request['request_id']))
        else:
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,0)',(uid,request['request_id'],hashlib.sha256(canonical(request)).hexdigest(),json.dumps(request),json.dumps(result)))
        for card in result_view(request,result)['tasks']:
            card = submission.decorate(db,uid,request['request_id'],card)
            db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',
                (secrets.token_hex(16),uid,request['request_id'],json.dumps(card)))
        db.execute('INSERT INTO recognition_record_policies VALUES(?,?,?,?) ON CONFLICT(user_id,request_id,input_id) DO UPDATE SET policy=excluded.policy',
            (uid,request['request_id'],request['records'][0]['record']['input_id'],json.dumps(policy)))
    finally:
        recognition._ACTIVE.release()
