"""Deterministic source revisions and conservative protection of handled work."""
from copy import deepcopy
import hashlib
import json

from contracts.teaching_io import canonical

CHANGED_SOURCE_REVIEW = '此来源已有任务或处理记录；内容已变化，请核对后更新已有任务或确认新增，避免重复加入。'


def upgrade_pending_candidates(db):
    """Upgrade only the old source-change guard, never user resolutions.

    A successful source is skipped on later syncs, so this cannot wait for a
    rerun. Keep IDs/versions unchanged: only expose the already promised manual
    update option. Both the marker and authenticated source lineage must match.
    """
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='recognition_candidates'").fetchone():
        return
    rows = db.execute('''SELECT id,candidate FROM recognition_candidates c
        WHERE c.state='pending' AND c.resolution IS NULL AND EXISTS (
            SELECT 1 FROM teaching_submission_requests current
            JOIN teaching_submission_requests old ON old.user_id=current.user_id
                AND old.account=current.account AND old.input_id=current.input_id
            JOIN recognition_candidates prior ON prior.user_id=old.user_id AND prior.request_id=old.request_id
            WHERE current.user_id=c.user_id AND current.request_id=c.request_id
                AND old.request_id<>current.request_id)''').fetchall()
    for cid, value in rows:
        card = json.loads(value)
        if (card.get('notice_type') == 'create' and card.get('needs_review') is True
                and CHANGED_SOURCE_REVIEW in card.get('review_reasons', [])):
            card['notice_type'] = 'uncertain'
            db.execute('UPDATE recognition_candidates SET candidate=? WHERE id=?', (json.dumps(card), cid))


def source_digest(entry, assignment, files, *, include_legacy_gradebook_id=False):
    stable = deepcopy(entry)
    # A cross-reference alone does not change the facts to extract.
    if not include_legacy_gradebook_id:
        stable.get('platform_item', {}).pop('gradebook_id', None)
    record = stable['record']
    for key in ('collected_at', 'snapshot_id'):
        record.pop(key, None)
    assets = {a['ref_id']: (a['sha256'], a['mime_type']) for a in files}
    for ref in record['references']:
        if ref['ref_id'] in assets:
            # The authenticated bytes define the document, not a transient
            # parser failure or a varying transcript of those same bytes.
            ref.pop('text', None)
            ref.pop('read_state', None)
            ref['file_identity'] = assets[ref['ref_id']]
    record['references'].sort(key=lambda r: r['ref_id'])
    material = dict(entry=stable, assignment=assignment)
    return hashlib.sha256(canonical(material)).hexdigest()


def initialize_gradebook(db):
    db.execute("""CREATE TABLE IF NOT EXISTS teaching_gradebook_links(
        user_id TEXT NOT NULL, account TEXT NOT NULL, input_id TEXT NOT NULL,
        course_id TEXT NOT NULL, gradebook_id TEXT NOT NULL,
        PRIMARY KEY(user_id,account,input_id))""")


def record_gradebook_link(db, uid, account, entry):
    column = entry.get('platform_item', {}).get('gradebook_id')
    if column:
        record = entry['record']
        db.execute("""INSERT INTO teaching_gradebook_links VALUES(?,?,?,?,?)
            ON CONFLICT(user_id,account,input_id) DO UPDATE SET
            course_id=excluded.course_id, gradebook_id=excluded.gradebook_id""",
            (uid, account, record['input_id'], record['course_id'], column))


def retire_covered_gradebook(db, uid, account):
    """A successful native result replaces only untouched provisional gradebook cards."""
    import submission_runtime as controls
    rows = db.execute("""SELECT c.id FROM recognition_candidates c
        JOIN teaching_submission_requests g ON g.user_id=c.user_id AND g.request_id=c.request_id
        JOIN recognition_batches gb ON gb.user_id=c.user_id AND gb.request_id=c.request_id
        JOIN teaching_gradebook_links l ON l.user_id=g.user_id AND l.account=g.account
          AND l.gradebook_id=json_extract(gb.request,'$.records[0].record.source_item_id')
          AND l.course_id=json_extract(gb.request,'$.records[0].record.course_id')
        JOIN teaching_sources n ON n.user_id=l.user_id AND n.account=l.account AND n.input_id=l.input_id
        JOIN recognition_batches nb ON nb.user_id=n.user_id AND nb.request_id=n.request_id
        WHERE c.user_id=? AND g.account=? AND c.state='pending' AND c.version=1 AND c.resolution IS NULL
          AND json_extract(gb.request,'$.records[0].platform_item.kind')='gradebook_assignment'
          AND json_extract(nb.result,'$.status') IN ('success','no_tasks')
          AND NOT EXISTS (SELECT 1 FROM teaching_gradebook_links other
            WHERE other.user_id=l.user_id AND other.account=l.account AND other.course_id=l.course_id
              AND other.gradebook_id=l.gradebook_id AND other.input_id<>l.input_id)""", (uid, account)).fetchall()
    protected = controls.manually_controlled_candidates(db, uid, rows)
    db.executemany("DELETE FROM recognition_candidates WHERE user_id=? AND id=? AND state='pending' AND version=1 AND resolution IS NULL",
                   [(uid, row['id']) for row in rows if row['id'] not in protected])


GRADEBOOK_REVIEW = '本课程已处理过可能对应的成绩明细条目，请核对后再加入。'


def previous_gradebook_card(db, uid, request_id):
    # The native detail and the gradebook row use different Blackboard IDs.
    # A shared name can withhold auto-apply, but never authorizes a state change.
    return db.execute('''SELECT 1 FROM teaching_submission_requests current
        JOIN teaching_submission_sources native ON native.user_id=current.user_id
          AND native.account=current.account AND native.input_id=current.input_id
        JOIN recognition_batches cb ON cb.user_id=current.user_id AND cb.request_id=current.request_id
        JOIN teaching_submission_requests old ON old.user_id=current.user_id AND old.account=current.account
          AND old.input_id LIKE 'pku:gradebook:%'
        JOIN recognition_batches ob ON ob.user_id=old.user_id AND ob.request_id=old.request_id
        WHERE current.user_id=? AND current.request_id=?
          AND json_extract(ob.request,'$.records[0].record.course_id')=json_extract(cb.request,'$.records[0].record.course_id')
          AND (json_extract(ob.request,'$.records[0].source.label')=json_extract(cb.request,'$.records[0].source.label')
               OR json_extract(ob.request,'$.records[0].record.source_item_id')=json_extract(cb.request,'$.records[0].platform_item.gradebook_id')
               OR EXISTS (SELECT 1 FROM teaching_gradebook_links l WHERE l.user_id=current.user_id AND l.account=current.account
                   AND l.input_id=current.input_id AND l.gradebook_id=json_extract(ob.request,'$.records[0].record.source_item_id')))
          AND EXISTS (SELECT 1 FROM recognition_candidates c WHERE c.user_id=old.user_id AND c.request_id=old.request_id
                      AND (c.state!='pending' OR c.version>1 OR c.resolution IS NOT NULL))
        LIMIT 1''', (uid, request_id)).fetchone()


def protect_source_changes(db, uid, request_id, cards):
    """Do not use index-based model candidate IDs as cross-version task IDs.

    Genuine new actions remain reviewable. Existing tasks, manual edits,
    completion and deletion decisions are never overwritten or resurrected.
    The authenticated account/source binding prevents cross-account matches.
    """
    previous = db.execute('''SELECT 1 FROM teaching_submission_requests current
        JOIN teaching_submission_requests old ON old.user_id=current.user_id
          AND old.account=current.account AND old.input_id=current.input_id
        JOIN recognition_candidates c ON c.user_id=old.user_id AND c.request_id=old.request_id
        WHERE current.user_id=? AND current.request_id=? AND old.request_id<>current.request_id
        LIMIT 1''', (uid, request_id)).fetchone()
    reason = CHANGED_SOURCE_REVIEW if previous else None
    if not reason and any(card['notice_type'] == 'create' for card in cards) and previous_gradebook_card(db, uid, request_id):
        reason = GRADEBOOK_REVIEW
    if reason:
        for card in cards:
            if card['notice_type'] == 'create':
                card['notice_type'] = 'uncertain'
                card['needs_review'] = True
                card['review_reasons'] = list(dict.fromkeys(card['review_reasons'] + [reason]))
    return cards


def reuse_unchanged_result(db, uid, request_id, result):
    """Reuse processing only when the latest verified source result is equal.

    Ignore retrieval context and per-attempt candidate IDs, NOT evidence,
    deadlines, decisions, titles, descriptions, or primary attachment text.
    Compare a multiset so model output order is never a cross-version identity.
    No task/candidate state is mutated, including manual/deleted/ignored work.
    """
    prior = db.execute('''SELECT b.result FROM recognition_batches b
        WHERE b.user_id=? AND b.request_id<>? AND EXISTS (
          SELECT 1 FROM teaching_submission_requests current
          JOIN teaching_submission_requests old ON old.user_id=current.user_id
            AND old.account=current.account AND old.input_id=current.input_id
          WHERE current.user_id=b.user_id AND current.request_id=? AND old.request_id=b.request_id)
        ORDER BY b.rowid DESC LIMIT 1''', (uid, request_id, request_id)).fetchone()
    if not prior or result.get('status') not in ('success', 'no_tasks'): return False
    old = json.loads(prior[0])
    if old.get('status') not in ('success', 'no_tasks'): return False

    return decision_proof(old) == decision_proof(result)


def decision_proof(value):
    rows = []
    for raw in value['record_results']:
        row = deepcopy(raw)
        decisions = row.pop('decisions')
        normalized = []
        for d in decisions:
            record = d['pair']['record']
            record.pop('course_context', None)
            record['references'] = [r for r in record['references'] if r['kind'] != 'course_context']
            d['pair']['assessment'].pop('candidate_id', None)
            normalized.append(canonical(d))
        row['decisions'] = [json.loads(d) for d in sorted(normalized)]
        rows.append(canonical(row))
    return sorted(rows)


def supersede_unhandled(db, uid, request_id, snapshot_id):
    """A context-only revision replaces untouched proposals of the same capture."""
    import submission_runtime as controls
    rows=db.execute('''SELECT c.*,b.request AS old_request FROM teaching_submission_requests current
        JOIN teaching_submission_requests old ON old.user_id=current.user_id
            AND old.account=current.account AND old.input_id=current.input_id
        JOIN recognition_candidates c ON c.user_id=old.user_id AND c.request_id=old.request_id
        JOIN recognition_batches b ON b.user_id=c.user_id AND b.request_id=c.request_id
        WHERE current.user_id=? AND current.request_id=? AND old.request_id<>current.request_id
            AND c.state='pending' AND c.version=1 AND c.resolution IS NULL''',(uid,request_id)).fetchall()
    protected=controls.manually_controlled_candidates(db,uid,rows)
    for row in rows:
        original=json.loads(row['old_request'])
        same=any(e.get('record',{}).get('snapshot_id')==snapshot_id for e in original.get('records',[]))
        if same and row['id'] not in protected:
            db.execute("DELETE FROM recognition_candidates WHERE id=? AND user_id=? AND state='pending' AND version=1 AND resolution IS NULL",(row['id'],uid))
