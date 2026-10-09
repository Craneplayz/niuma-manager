"""Account-bound native assignment source exclusions; no model-derived completion."""
import json
from datetime import datetime


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS teaching_submission_sources(
        user_id TEXT NOT NULL, account TEXT NOT NULL, input_id TEXT NOT NULL,
        state TEXT NOT NULL, observed_at TEXT NOT NULL, review_pending INTEGER NOT NULL,
        PRIMARY KEY(user_id,account,input_id));
    CREATE TABLE IF NOT EXISTS teaching_submission_requests(
        user_id TEXT NOT NULL, account TEXT NOT NULL, request_id TEXT NOT NULL, input_id TEXT NOT NULL,
        PRIMARY KEY(user_id,account,request_id));
    ''')


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
    current = source_state(db,uid,account,record['input_id'])
    if current and current['state']=='submitted':
        reason=json.dumps(dict(action='source_submitted',input_id=record['input_id'],request_id=request_id))
        db.execute('''UPDATE recognition_candidates SET state='ignored',version=version+1,resolution=?
            WHERE user_id=? AND state='pending' AND request_id IN
              (SELECT request_id FROM teaching_submission_requests WHERE user_id=? AND account=? AND input_id=?)''',
            (reason,uid,uid,account,record['input_id']))
    return current


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


def guard(db, uid, request_id, ApiError):
    state=for_request(db,uid,request_id)
    if state and state['state']=='submitted':
        raise ApiError(409,'该原生作业已提交，已退出识别及待审核队列；原记录已保留')
    return state


def decorate(db, uid, request_id, card):
    state=for_request(db,uid,request_id)
    if state and state['review_pending']:
        card['needs_review']=True
        reason='该作业曾显示已提交，当前提交状态发生变化，请人工确认是否需要重新完成。'
        card['review_reasons']=list(dict.fromkeys(card['review_reasons']+[reason]))
    return card


def project_result(db, uid, request_id, result):
    """Hide obsolete retry buttons too, without rewriting the stored model run."""
    state=for_request(db,uid,request_id)
    if state and state['state']=='submitted':
        result.update(status='no_tasks',tasks=[],errors=[])
        for row in result['record_results']:
            row.update(status='no_tasks',task_ids=[],warnings=['source_submitted:原生作业已提交，已退出识别与审核队列。'])
    return result
