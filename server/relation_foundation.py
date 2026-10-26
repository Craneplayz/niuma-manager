"""Project existing authenticated records and persist non-executing relation drafts.

No crawler/model calls, task writes, completion propagation or course-scope changes.
"""
from datetime import datetime, timezone
import json
import secrets

from jsonschema import ValidationError
from contracts import relation_foundation as contract
import submission_runtime as controls


class FoundationError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS relation_foundation_proposals(
        id TEXT PRIMARY KEY,user_id TEXT NOT NULL,account TEXT NOT NULL,
        operation_id TEXT NOT NULL,digest TEXT NOT NULL,context TEXT NOT NULL,
        relation TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(user_id,account,operation_id));
      CREATE TABLE IF NOT EXISTS relation_foundation_events(
        id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL,user_id TEXT NOT NULL,
        account TEXT NOT NULL,event TEXT NOT NULL,created_at TEXT NOT NULL);
    ''')


def authorized(db, uid, account):
    if not isinstance(account, str) or not account or controls.current_account(db, uid) != account:
        raise FoundationError('not_found', '来源账号不存在或不可访问')


def part(part_id, text, state, sha256=None, label=None):
    text = text if isinstance(text, str) else ''
    if state == 'summary':
        state = 'partial'
    state = state if state in ('complete', 'partial', 'unread', 'unsupported', 'failed') else 'unread'
    if len(text) > 24000:
        text, state = text[:24000], 'partial'
    value = dict(part_id=part_id, label=str(label or part_id)[:1024], text=text, read_state=state, sha256=sha256)
    return dict(value, revision=contract.digest(value))


def context(db, uid, account, input_ids):
    """Caller owns the read transaction; all revisions come from one DB snapshot."""
    authorized(db, uid, account)
    if (not isinstance(input_ids, list) or not 1 <= len(input_ids) <= 20
            or any(not isinstance(i, str) or not i or len(i) > 10000 for i in input_ids)
            or len(set(input_ids)) != len(input_ids)):
        raise FoundationError('invalid_selection', '请选择1至20个互不重复的来源')
    sources, bindings, warnings, paused = [], {}, [], set()
    for iid in sorted(input_ids):
        row = db.execute('SELECT * FROM teaching_sources WHERE user_id=? AND account=? AND input_id=?',
                         (uid, account, iid)).fetchone()
        if not row:
            raise FoundationError('not_found', '所选来源不存在或不可访问')
        entry = json.loads(row['request'])['records'][0]
        record = entry['record']
        term = (record.get('course_term') or {}).get('term_id')
        sid = contract.identity('source', uid, account, record['course_id'], term, iid, record.get('source_item_id'))
        parts = [part('body', record.get('text'), record.get('body_status', 'complete'))]
        if record.get('source_metadata_text'):
            parts.append(part('metadata', record['source_metadata_text'], 'complete'))
        files = {r['ref_id']: r for r in db.execute('''SELECT r.ref_id,r.sha256,f.cache_version,f.result
            FROM teaching_asset_refs r JOIN teaching_asset_files f USING(user_id,account,sha256,mime_type)
            WHERE r.user_id=? AND r.account=? AND r.request_id=?''', (uid, account, row['request_id']))}
        for ref in record.get('references', []):
            if ref['kind'] == 'course_context':
                continue  # Evidence retains its own source; never launder borrowed text.
            text, state, sha = ref.get('text'), ref.get('read_state', 'unread'), None
            file = files.get(ref['ref_id'])
            if file:
                sha = file['sha256']
                cached = json.loads(file['result']) if file['result'] else {}
                if state != 'complete' and cached.get('read_state') == 'complete':
                    text, state = cached.get('text'), 'complete'
            parts.append(part('ref:' + ref['ref_id'], text, state, sha, ref.get('label')))
        if len(parts) > 100:
            raise FoundationError('context_limit', '来源附件过多，请缩小选择范围')
        if any(p['read_state'] != 'complete' for p in parts):
            warnings.append('incomplete_evidence:' + sid)
        native = controls.find(db, uid, account, iid)
        submission = None
        if native:
            obs = json.loads(native['detail'])['observation']
            snap = obs['snapshot']
            if (snap['source_account_id'] == account and snap['input_id'] == iid
                    and snap['course_id'] == record['course_id'] and snap['content_id'] == record.get('source_item_id')):
                submission = dict(state=snap['state'], basis=snap['basis'], snapshot_id=snap['snapshot_id'],
                                  sequence=snap['sequence'], control_revision=obs['control_revision'],
                                  review_pending=obs['review_pending'], scope=obs.get('scope', 'unknown'),
                                  control_digest=contract.digest(obs))
                paused.update(a['action_id'] for a in obs.get('actions', []) if a.get('source_completion_paused'))
            else:
                warnings.append('submission_identity_mismatch:' + sid)
        source = dict(id=sid, input_id=iid, course_id=record['course_id'], content_id=record.get('source_item_id'),
                      term_id=term, label=str(entry['source'].get('label') or iid)[:1024],
                      course_name=entry['source'].get('course_or_project'), url=entry['source'].get('url'),
                      starts_on=(record.get('course_term') or {}).get('starts_on'),
                      ends_on=(record.get('course_term') or {}).get('ends_on'),
                      snapshot_id=record['snapshot_id'], parts=parts, submission=submission)
        source['revision'] = contract.digest(source)
        sources.append(source)
        bindings[iid] = sid
    terms = {s['term_id'] for s in sources}
    term = next(iter(terms)) if len(terms) == 1 and None not in terms else None
    if term is None:
        warnings.append('semester_unresolved')
    windows = {(s['starts_on'], s['ends_on']) for s in sources}
    if len(windows) != 1 or any(not start or not end or start > end for start, end in windows):
        warnings.append('semester_window_unresolved')
    # Keep consumed/ignored candidates: they prove provenance and user decisions.
    candidates, by_candidate = {}, {}
    for iid, sid in bindings.items():
        rows = db.execute('''SELECT c.*,b.request AS original_request FROM recognition_candidates c JOIN teaching_submission_requests r
            ON r.user_id=c.user_id AND r.request_id=c.request_id
            JOIN recognition_batches b ON b.user_id=c.user_id AND b.request_id=c.request_id
            WHERE r.user_id=? AND r.account=? AND r.input_id=?''', (uid, account, iid)).fetchall()
        for row in rows:
            card = json.loads(row['candidate'])
            if card.get('input_id') != iid:
                continue
            source = next(s for s in sources if s['id'] == sid)
            original = [e.get('record', {}) for e in json.loads(row['original_request']).get('records', [])]
            if not any(r.get('input_id') == iid and r.get('course_id') == source['course_id']
                       and r.get('source_item_id') == source['content_id']
                       and (r.get('course_term') or {}).get('term_id') == source['term_id'] for r in original):
                warnings.append('historical_source_identity_mismatch:' + sid)
                continue
            aid = contract.identity('candidate', uid, account, row['id'])
            value = candidates.setdefault(row['id'], dict(id=aid, kind='candidate', binding_id=row['id'],
                label=str(card.get('title', ''))[:1024], state=row['state'], revision=contract.digest(dict(row)),
                source_ids=[], protected=row['state'] != 'pending' or row['version'] != 1
                    or controls.identity('candidate', row['id']) in paused))
            if sid not in value['source_ids']:
                value['source_ids'].append(sid)
            by_candidate[row['id']] = aid
    controlled = controls.manually_controlled_candidates(db, uid, [{'id': cid} for cid in candidates])
    for cid in controlled:
        candidates[cid]['protected'] = True
    actions = list(candidates.values())
    transitions = []
    for task in controls.workspace(db, uid)[0]:
        linked = sorted({h['storageId'] for h in task.get('recognitionHistory', [])
                         if isinstance(h, dict) and h.get('storageId') in candidates
                         and candidates[h['storageId']]['state'] == 'applied'})
        if not linked:
            continue
        aid = contract.identity('task', uid, account, task['id'])
        history = [h.get('storageId') for h in task.get('recognitionHistory', []) if isinstance(h, dict)]
        if any(c not in candidates for c in history):
            warnings.append('partial_task_provenance:' + aid)
        actions.append(dict(id=aid, kind='task', binding_id=task['id'], label=str(task.get('title', ''))[:1024],
                            state='deleted' if task.get('recycleBin') else task['status'], revision=contract.digest(task),
                            source_ids=sorted({s for c in linked for s in candidates[c]['source_ids']}), protected=True))
        transitions.extend(dict(candidate_id=by_candidate[c], task_id=aid, basis='saved_history') for c in linked)
    for a in actions:
        a['source_ids'].sort()
    value = dict(contract_version=contract.VERSION, scope_id=contract.identity('scope', uid, account),
                 source_account_id=account, term_id=term, sources=sources, actions=sorted(actions, key=lambda a: a['id']),
                 transitions=sorted(transitions, key=lambda t: (t['candidate_id'], t['task_id'])), warnings=sorted(set(warnings)))
    value['revision'] = contract.digest(value)
    if len(actions) > 200 or len(transitions) > 500 or len(json.dumps(value, ensure_ascii=False).encode()) > 1_500_000:
        raise FoundationError('context_limit', '所选来源的行动或证据超过限制，请减少来源数量')
    try:
        return contract.check('context', value)
    except (ValueError, ValidationError) as error:
        raise FoundationError('stored_source_invalid', '已保存来源的字段、证据或提交状态不符合契约，请检查来源记录') from error


def receipt(row):
    return dict(id=row['id'], state='proposed', application_allowed=False,
                context_revision=json.loads(row['context'])['revision'], created_at=row['created_at'])


def record_proposal(db, uid, account, operation_id, expected_context, relation):
    """Internal producer/consumer boundary. Caller must use one write transaction.

    Stage one has no public proposal producer or apply operation. Stored proposals
    are structurally checked, not verified semantic relationships.
    """
    authorized(db, uid, account)
    if not db.in_transaction:
        raise FoundationError('transaction_required', '关系保存需要显式事务')
    if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
        raise FoundationError('invalid_operation', '无效的关系操作标识')
    contract.validate_relation(expected_context, relation)
    if expected_context['scope_id'] != contract.identity('scope', uid, account):
        raise FoundationError('not_found', '关系上下文不存在或不可访问')
    fingerprint = contract.digest(dict(context=expected_context, relation=relation))
    old = db.execute('SELECT * FROM relation_foundation_proposals WHERE user_id=? AND account=? AND operation_id=?',
                     (uid, account, operation_id)).fetchone()
    if old:
        if old['digest'] != fingerprint:
            raise FoundationError('operation_conflict', '同一操作标识对应的关系内容已变化')
        return receipt(old)
    current = context(db, uid, account, [s['input_id'] for s in expected_context['sources']])
    if current['revision'] != expected_context['revision']:
        raise FoundationError('stale_context', '来源、任务或提交状态已变化，请重新生成关系')
    contract.validate_relation(current, relation)
    pid, event_id = secrets.token_hex(16), secrets.token_hex(16)
    now = datetime.now(timezone.utc).isoformat()
    audit = dict(kind='proposal_recorded', actor=uid, operation_id=operation_id,
                 context_revision=current['revision'], relation_digest=contract.digest(relation),
                 previous_state=None, next_state='proposed', task_mutations=0)
    # Savepoint makes proposal and audit atomic even if a caller catches errors.
    db.execute('SAVEPOINT relation_foundation_save')
    try:
        db.execute('INSERT INTO relation_foundation_proposals VALUES(?,?,?,?,?,?,?,?)',
                   (pid, uid, account, operation_id, fingerprint, json.dumps(current), json.dumps(relation), now))
        db.execute('INSERT INTO relation_foundation_events VALUES(?,?,?,?,?,?)',
                   (event_id, pid, uid, account, json.dumps(audit), now))
        db.execute('RELEASE relation_foundation_save')
    except Exception:
        db.execute('ROLLBACK TO relation_foundation_save')
        db.execute('RELEASE relation_foundation_save')
        raise
    return receipt(db.execute('SELECT * FROM relation_foundation_proposals WHERE id=?', (pid,)).fetchone())


def read_proposal(db, uid, account, proposal_id):
    authorized(db, uid, account)
    row = db.execute('SELECT * FROM relation_foundation_proposals WHERE id=? AND user_id=? AND account=?',
                     (proposal_id, uid, account)).fetchone()
    if not row:
        raise FoundationError('not_found', '关系记录不存在或不可访问')
    return dict(receipt(row), context=json.loads(row['context']), relation=json.loads(row['relation']),
                audit=[json.loads(e[0]) for e in db.execute('''SELECT event FROM relation_foundation_events
                WHERE proposal_id=? AND user_id=? AND account=? ORDER BY rowid''', (proposal_id, uid, account))])


def route(handler, db, uid, path, body):
    if path != '/api/recognition/relation-context' or handler.command != 'POST':
        return False
    if set(body) != {'source_account_id', 'input_ids'}:
        handler.reply(400, dict(code='invalid_selection', message='只接受来源账号和来源标识列表'))
        return True
    try:
        # BEGIN fixes one consistent read snapshot, even while synchronization runs.
        if not db.in_transaction:
            db.execute('BEGIN')
        value = context(db, uid, body['source_account_id'], body['input_ids'])
        handler.reply(200, value)
    except FoundationError as error:
        status = 404 if error.code == 'not_found' else 500 if error.code == 'stored_source_invalid' else 400
        handler.reply(status, dict(code=error.code, message=error.message))
    return True
