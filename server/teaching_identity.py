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


def source_digest(entry, assignment, files):
    stable = deepcopy(entry)
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
    if previous:
        for card in cards:
            if card['notice_type'] == 'create':
                card['notice_type'] = 'uncertain'
                card['needs_review'] = True
                card['review_reasons'] = list(dict.fromkeys(card['review_reasons'] + [CHANGED_SOURCE_REVIEW]))
    return cards


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
