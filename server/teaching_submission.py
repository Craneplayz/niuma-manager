"""Account-bound native status with submission-policy/0.1 unknown-scope protection.

This runtime has no verified permanent action mapping yet. All captured sources
therefore have unknown scope: a submitted page cannot withdraw any action or
skip recognition. The persistent reopened-review latch takes precedence.
"""
import json
from datetime import datetime, timezone

SCOPE_REVIEW = '原生作业显示已提交，但尚未明确对应哪些行动，请核对后决定是否保留。'
STATUS_REVIEW = '该作业曾显示已提交，提交状态发生变化；来源核对状态尚未明确解除。'


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS teaching_submission_sources(
        user_id TEXT NOT NULL, account TEXT NOT NULL, input_id TEXT NOT NULL,
        state TEXT NOT NULL, observed_at TEXT NOT NULL, review_pending INTEGER NOT NULL,
        PRIMARY KEY(user_id,account,input_id));
    CREATE TABLE IF NOT EXISTS teaching_submission_requests(
        user_id TEXT NOT NULL, account TEXT NOT NULL, request_id TEXT NOT NULL, input_id TEXT NOT NULL,
        PRIMARY KEY(user_id,account,request_id));
    CREATE TABLE IF NOT EXISTS teaching_submission_recoveries(
        candidate_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,
        previous_resolution TEXT NOT NULL,recovered_at TEXT NOT NULL);
    ''')
    # Recover only this draft's automatic whole-source withdrawals. Keep the
    # original reason in an audit row and never reopen a user's ignore/delete.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recognition_candidates'").fetchone():
        return
    for row in db.execute('''SELECT c.* FROM recognition_candidates c
        WHERE c.state='ignored' AND c.resolution IS NOT NULL
        AND NOT EXISTS(SELECT 1 FROM teaching_submission_recoveries a WHERE a.candidate_id=c.id)''').fetchall():
        try: reason = json.loads(row['resolution'])
        except (ValueError,TypeError): continue
        if not isinstance(reason,dict) or reason.get('action')!='source_submitted': continue
        bound = db.execute('''SELECT 1 FROM teaching_submission_requests
            WHERE user_id=? AND request_id=? AND input_id=?''',
            (row['user_id'],row['request_id'],reason.get('input_id'))).fetchone()
        if not bound: continue
        db.execute('INSERT INTO teaching_submission_recoveries VALUES(?,?,?,?)',
            (row['id'],row['user_id'],row['resolution'],datetime.now(timezone.utc).isoformat()))
        db.execute("UPDATE recognition_candidates SET state='pending',version=version+1,resolution=NULL WHERE id=?",
                   (row['id'],))


def source_state(db, uid, account, input_id):
    return db.execute('SELECT * FROM teaching_submission_sources WHERE user_id=? AND account=? AND input_id=?',
                      (uid,account,input_id)).fetchone()


def stale(previous, record, assignment):
    if not previous or not assignment: return False
    old = datetime.fromisoformat(previous['observed_at'].replace('Z','+00:00'))
    new = datetime.fromisoformat(record['collected_at'].replace('Z','+00:00'))
    return new < old or (new == old and assignment['state'] != previous['state'])


def observe(db, uid, account, record, request_id, assignment, old_request_id=None):
    # Bind only versions actually registered under this campus account. Do not
    # guess the account of older batches from their title/course/input alone.
    for rid in (old_request_id, request_id):
        if rid:
            db.execute('INSERT OR IGNORE INTO teaching_submission_requests VALUES(?,?,?,?)',
                       (uid,account,rid,record['input_id']))
    previous = source_state(db,uid,account,record['input_id'])
    if assignment:
        review = bool(previous and (previous['review_pending'] or
                      previous['state']=='submitted' and assignment['state']!='submitted'))
        db.execute('''INSERT INTO teaching_submission_sources VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id,account,input_id) DO UPDATE SET state=excluded.state,
            observed_at=excluded.observed_at,review_pending=excluded.review_pending''',
            (uid,account,record['input_id'],assignment['state'],record['collected_at'],int(review)))
    # Unknown action relations must remain intact, even on a submitted page.
    return source_state(db,uid,account,record['input_id'])


def for_request(db, uid, request_id):
    # Credential removal/switching invalidates this lookup, never granting trust
    # to a same-named assignment belonging to another account.
    from teaching import account
    cred=db.execute('SELECT username FROM source_credentials WHERE user_id=?',(uid,)).fetchone()
    if not cred: return None
    return db.execute('''SELECT s.* FROM teaching_submission_requests r JOIN teaching_submission_sources s
        ON s.user_id=r.user_id AND s.account=r.account AND s.input_id=r.input_id
        WHERE r.user_id=? AND r.account=? AND r.request_id=?''',
        (uid,account(cred['username']),request_id)).fetchone()


def skip_model(state):
    # submission-policy/0.1: unknown scope is not enough to skip a model call;
    # review_pending, however, pauses all later calls until explicit resolution.
    return bool(state and state['review_pending'])


def review_reason(state):
    if skip_model(state): return STATUS_REVIEW
    if state and state['state']=='submitted': return SCOPE_REVIEW
    return None


def guard_model(db, uid, request_id, ApiError):
    state=for_request(db,uid,request_id)
    if skip_model(state):
        raise ApiError(409,STATUS_REVIEW)
    return state


def guard_auto_apply(db, uid, request_id, ApiError):
    state=for_request(db,uid,request_id)
    reason=review_reason(state)
    if reason: raise ApiError(400,reason)
    return state


def decorate(db, uid, request_id, card):
    state=for_request(db,uid,request_id)
    reason=review_reason(state)
    if reason:
        card['needs_review']=True
        card['review_reasons']=list(dict.fromkeys(card['review_reasons']+[reason]))
    return card


def project_result(db, uid, request_id, result):
    """Keep unknown/independent actions and failures visible; never erase a run."""
    state=for_request(db,uid,request_id)
    reason=review_reason(state)
    if reason:
        code='submission_status_changed' if skip_model(state) else 'submission_scope_unknown'
        for card in result['tasks']:
            card['needs_review']=True
            card['review_reasons']=list(dict.fromkeys(card['review_reasons']+[reason]))
        for row in result['record_results']:
            row['warnings']=list(dict.fromkeys(row['warnings']+[code+':'+reason]))
    return result
