"""Persistent account-scoped implementation of submission-operations/0.1."""
import hashlib
import json
import secrets
import re
from jsonschema import ValidationError
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs

from contracts import submission_policy as policy, submission_operations as operations

BASES = {'submitted': 'review_submission_history_title', 'active': 'active_assignment_page', 'unknown': 'unknown'}


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS submission_controls(
      record_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,account TEXT NOT NULL,input_id TEXT NOT NULL,
      content_digest TEXT NOT NULL,detail TEXT NOT NULL,UNIQUE(user_id,account,input_id));
    CREATE TABLE IF NOT EXISTS submission_operations(
      user_id TEXT NOT NULL,operation_id TEXT NOT NULL,digest TEXT NOT NULL,record_id TEXT NOT NULL,
      receipt TEXT NOT NULL,audit TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(user_id,operation_id));
    CREATE TABLE IF NOT EXISTS submission_events(
      id TEXT PRIMARY KEY,user_id TEXT NOT NULL,record_id TEXT NOT NULL,event TEXT NOT NULL,created_at TEXT NOT NULL);
    ''')


def identity(kind, raw):
    return kind + ':' + hashlib.sha256(raw.encode()).hexdigest()


def label(value):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or '作业')).strip()[:512] or '作业'


def current_account(db, uid):
    from teaching import account
    row = db.execute('SELECT username FROM source_credentials WHERE user_id=?', (uid,)).fetchone()
    return account(row['username']) if row else None


def find(db, uid, account, input_id):
    return db.execute('SELECT * FROM submission_controls WHERE user_id=? AND account=? AND input_id=?',
                      (uid, account, input_id)).fetchone()


def persist(db, row, detail):
    operations.check('detail', detail)
    db.execute('UPDATE submission_controls SET detail=? WHERE record_id=?', (json.dumps(detail), row['record_id']))


def workspace(db, uid):
    row = db.execute('SELECT value,version FROM workspace WHERE user_id=? AND name="tasks"', (uid,)).fetchone()
    return (json.loads(row['value']), row['version']) if row else ([], 0)


def linked_actions(db, row, detail):
    """Use saved candidate IDs and task history, never title/course similarity."""
    old = {a['action_id']: a for a in detail['observation']['actions']}
    labels = {a['action_id']: a['label'] for a in detail['action_labels']}
    candidates = db.execute('''SELECT c.* FROM recognition_candidates c JOIN teaching_submission_requests r
      ON r.user_id=c.user_id AND r.request_id=c.request_id WHERE r.user_id=? AND r.account=? AND r.input_id=?''',
      (row['user_id'], row['account'], row['input_id'])).fetchall()
    by_id = {c['id']: c for c in candidates}
    actions, refs, used = [], {}, set()
    for task in workspace(db, row['user_id'])[0]:
        history = [h for h in task.get('recognitionHistory', []) if isinstance(h, dict)
                   and h.get('storageId') in by_id and by_id[h['storageId']]['state'] == 'applied']
        if not history: continue
        aid = identity('task', task['id']); prior = old.get(aid)
        # An explicitly linked pending candidate retains its link when confirmed.
        inherited = [old.get(identity('candidate', h['storageId'])) for h in history]
        inherited = [a for a in inherited if a and a['relation'] != 'unknown']
        if not prior and inherited and len({a['relation'] for a in inherited}) == 1: prior = inherited[0]
        deleted = task.get('recycleBin') or task['status'] == 'cancelled'
        completed = task['status'] == 'completed' and not deleted
        relation = prior['relation'] if prior else 'unknown'
        origin = (prior.get('completion_origin') if prior else None) or 'manual'
        action = dict(action_id=aid, kind='task', relation=relation,
                      link_basis=prior['link_basis'] if prior else 'unresolved',
                      decision='deleted' if deleted else 'completed' if completed else 'confirmed',
                      completion_origin=origin if completed else None,
                      source_completion_paused=bool(prior and prior['source_completion_paused']))
        actions.append(action); refs[aid] = task['id']; labels[aid] = label(task['title'])
        used.update(h['storageId'] for h in history)
    for c in candidates:
        if c['state'] == 'applied' or c['id'] in used: continue
        aid = identity('candidate', c['id']); prior = old.get(aid)
        actions.append(dict(action_id=aid, kind='candidate', relation=prior['relation'] if prior else 'unknown',
            link_basis=prior['link_basis'] if prior else 'unresolved', decision='pending' if c['state']=='pending' else 'ignored',
            completion_origin=None, source_completion_paused=False))
        refs[aid]=c['id']; labels[aid]=label(json.loads(c['candidate'])['title'])
    # Tombstones prevent deleted user choices from being mistaken for new actions.
    present={a['action_id'] for a in actions}
    for aid, prior in old.items():
        if aid not in present:
            actions.append(dict(prior,decision='deleted',completion_origin=None))
    return actions, refs, [dict(action_id=a['action_id'],label=labels[a['action_id']]) for a in actions]


def refresh(db, row):
    detail=json.loads(row['detail']); observation=detail['observation']
    actions, refs, labels=linked_actions(db,row,detail)
    if actions!=observation['actions'] or labels!=detail['action_labels']:
        observation['actions']=actions; detail['action_labels']=labels
        if any(a['relation']!='assignment_submission' for a in actions): observation['scope']='unknown'
        observation['control_revision']+=1; persist(db,row,detail)
    return detail, refs


def observe(db, uid, account, record, assignment, previous, *, source=None, assets=()):
    if not assignment or not record.get('course_id') or not record.get('source_item_id'): return
    row=find(db,uid,account,record['input_id'])
    if row:
        snap=json.loads(row['detail'])['observation']['snapshot']
        if snap['course_id']!=record['course_id'] or snap['content_id']!=record['source_item_id']:
            db.execute('INSERT INTO submission_events VALUES(?,?,?,?,?)',(secrets.token_hex(16),uid,row['record_id'],
                        json.dumps(dict(kind='identity_changed',previous=json.loads(row['detail']))),datetime.now(timezone.utc).isoformat()))
            db.execute('DELETE FROM submission_controls WHERE record_id=?',(row['record_id'],));row=None
    content=deepcopy(record)
    for key in ('collected_at','snapshot_id'): content.pop(key,None)
    digest=hashlib.sha256(policy.canonical(dict(record=content,source=source,assets=assets))).hexdigest()
    if not row:
        rid='submission:'+secrets.token_hex(16)
        observation=dict(contract_version=policy.VERSION,snapshot=dict(source_account_id=account,input_id=record['input_id'],
            course_id=record['course_id'],content_id=record['source_item_id'],snapshot_id='submission-snapshot:'+secrets.token_hex(16),
            sequence=1,observed_at=record['collected_at'],state=assignment['state'],basis=BASES[assignment['state']]),
            scope='unknown',actions=[],control_revision=0,review_pending=bool(previous and previous['review_pending']))
        title=next((line.strip() for line in record.get('text','').splitlines() if line.strip()),record['input_id'])
        detail=dict(contract_version=operations.VERSION,record_id=rid,label=label(title),
                    course_or_project=label(source['course_or_project']) if source and source.get('course_or_project') else None,observation=observation,action_labels=[])
        operations.check('detail',detail)
        db.execute('INSERT INTO submission_controls VALUES(?,?,?,?,?,?)',(rid,uid,account,record['input_id'],digest,json.dumps(detail)))
        row=find(db,uid,account,record['input_id'])
    detail, refs=refresh(db,row); obs=detail['observation']; before=deepcopy(detail)
    old_state=previous['state'] if previous else 'unknown'
    snap=obs['snapshot']
    if snap['observed_at']!=record['collected_at'] or snap['state']!=assignment['state']:
        snap.update(sequence=snap['sequence']+1,snapshot_id='submission-snapshot:'+secrets.token_hex(16),
                    observed_at=record['collected_at'],state=assignment['state'],basis=BASES[assignment['state']])
    if row['content_digest']!=digest:
        obs['scope']='unknown';obs['control_revision']+=1
        db.execute('UPDATE submission_controls SET content_digest=? WHERE record_id=?',(digest,row['record_id']))
    decision=policy.decide(obs,verified_digest=policy.observation_digest(obs),source_account_id=account,
                           input_id=record['input_id'],previous_state=old_state)
    obs.update(review_pending=decision['review_pending'],control_revision=decision['control_revision'])
    tasks, version=workspace(db,uid); changed=False; now=datetime.now(timezone.utc)
    for aid in decision['complete_action_ids']:
        task=next(t for t in tasks if t['id']==refs[aid]);task['status']='completed'
        task['completedAt']=now.date().isoformat()
        kept=[]
        for plan in task.get('plans',[]):
            try: future=datetime.fromisoformat(plan['startAt'].replace('Z','+00:00'))>now
            except (ValueError,KeyError,TypeError): future=False
            if not future: kept.append(plan)
        task['plans']=kept;changed=True
        next(a for a in obs['actions'] if a['action_id']==aid).update(decision='completed',completion_origin='source_submission')
    if changed:
        db.execute('INSERT INTO workspace VALUES(?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value,version=excluded.version',
                   (uid,'tasks',json.dumps(tasks),version+1))
    for aid in decision['withdraw_candidate_ids']:
        # A distinct reason avoids the legacy recovery migration reopening these.
        db.execute("UPDATE recognition_candidates SET state='ignored',version=version+1,resolution=? WHERE id=? AND user_id=? AND state='pending'",
                   (json.dumps(dict(action='submission_policy_completed',record_id=row['record_id'])),refs[aid],uid))
        next(a for a in obs['actions'] if a['action_id']==aid)['decision']='ignored'
    persist(db,row,detail)
    if before!=detail:
        db.execute('INSERT INTO submission_events VALUES(?,?,?,?,?)',(secrets.token_hex(16),uid,row['record_id'],
                    json.dumps(dict(before=before,after=detail,decision=decision)),now.isoformat()))
    db.execute('UPDATE teaching_submission_sources SET review_pending=? WHERE user_id=? AND account=? AND input_id=?',
               (int(obs['review_pending']),uid,account,record['input_id']))


def control_for_request(db,uid,request_id):
    account=current_account(db,uid)
    row=db.execute('''SELECT c.* FROM submission_controls c JOIN teaching_submission_requests r
      ON c.user_id=r.user_id AND c.account=r.account AND c.input_id=r.input_id
      WHERE r.user_id=? AND r.account=? AND r.request_id=?''',(uid,account,request_id)).fetchone()
    return json.loads(row['detail'])['observation'] if row else None


def token(db,uid,request_id):
    obs=control_for_request(db,uid,request_id)
    return (obs['snapshot']['snapshot_id'],obs['control_revision']) if obs else None


def refresh_request(db,uid,request_id):
    obs=control_for_request(db,uid,request_id)
    if obs:
        row=find(db,uid,obs['snapshot']['source_account_id'],obs['snapshot']['input_id'])
        refresh(db,row)


def manual_task_write(db,uid,tasks):
    old={t['id']:t for t in workspace(db,uid)[0]}
    reopened={identity('task',t['id']) for t in tasks if t['status']!='completed' and old.get(t['id'],{}).get('status')=='completed'}
    if not reopened:return
    for row in db.execute('SELECT * FROM submission_controls WHERE user_id=?',(uid,)).fetchall():
        detail,_=refresh(db,row);obs=detail['observation'];changed=False
        for action in obs['actions']:
            if action['action_id'] in reopened and action['relation']=='assignment_submission':
                action.update(source_completion_paused=True,decision='confirmed',completion_origin=None);changed=True
        if changed:
            obs['control_revision']+=1;persist(db,row,detail)
            db.execute('INSERT INTO submission_events VALUES(?,?,?,?,?)',(secrets.token_hex(16),uid,row['record_id'],
                       json.dumps(dict(kind='manual_reopen',action_ids=sorted(reopened))),datetime.now(timezone.utc).isoformat()))


def route(handler,db,uid,path,body,ApiError):
    root='/api/sources/submissions'
    if path!=root and not path.startswith(root+'/'):return False
    account=current_account(db,uid)
    if not account:raise ApiError(404,'请先连接教学网')
    if handler.headers.get('Authorization'):raise ApiError(403,'此操作需要登录会话')
    db.execute('BEGIN IMMEDIATE')
    if path==root and handler.command=='GET':
        args=parse_qs(urlsplit(handler.path).query); requested=args.get('source_account_id',[None])[0];cursor=args.get('cursor',[''])[0]
        if requested!=account:raise ApiError(404,'来源不存在或已移除')
        if cursor and not db.execute('SELECT 1 FROM submission_controls WHERE user_id=? AND account=? AND record_id=?',(uid,account,cursor)).fetchone():raise ApiError(404,'分页位置已失效，请重新读取')
        rows=db.execute('SELECT * FROM submission_controls WHERE user_id=? AND account=? AND record_id>? ORDER BY record_id LIMIT 101',(uid,account,cursor)).fetchall()
        records=[]
        for row in rows[:100]:
            detail,_=refresh(db,row);records.append({k:detail[k] for k in ('record_id','label','course_or_project')})
        handler.reply(200,dict(contract_version=operations.VERSION,source_account_id=account,records=records,next_cursor=rows[99]['record_id'] if len(rows)>100 else None));return True
    rid=path[len(root)+1:].split('/')[0]
    row=db.execute('SELECT * FROM submission_controls WHERE record_id=? AND user_id=? AND account=?',(rid,uid,account)).fetchone()
    if not row:raise ApiError(404,'作业记录不存在或来源已移除')
    detail,refs=refresh(db,row)
    if path==root+'/'+rid and handler.command=='GET':handler.reply(200,detail);return True
    if path==root+'/'+rid+'/source' and handler.command=='GET':
        from teaching import request_view
        saved=db.execute('SELECT request FROM teaching_sources WHERE user_id=? AND account=? AND input_id=?',(uid,account,row['input_id'])).fetchone()
        if not saved:raise ApiError(404,'来源原文已移除')
        handler.reply(200,request_view(json.loads(saved['request'])));return True
    if path!=root+'/'+rid+'/operations' or handler.command!='POST':raise ApiError(404,'操作不存在')
    try:
        operations.check('operation',body)
        if body['record_id']!=rid:raise ValueError('identity')
        digest=operations.request_digest(body)
    except (ValueError,TypeError,KeyError,ValidationError) as e:
        raise ApiError(400,'作业状态操作格式不正确') from e
    saved=db.execute('SELECT * FROM submission_operations WHERE user_id=? AND operation_id=?',(uid,body['operation_id'])).fetchone()
    if saved:
        if saved['digest']!=digest:raise ApiError(409,'操作编号已用于不同请求，请刷新后重试')
        handler.reply(200,json.loads(saved['receipt']));return True
    try:plan=operations.apply_operation(detail,body)
    except operations.OperationConflict:raise ApiError(409,'来源或操作版本已变化，请刷新后重新确认') from None
    except (ValueError,KeyError,TypeError,ValidationError):raise ApiError(400,'当前状态不允许此操作，请刷新后核对选择') from None
    changed=plan['record'];persist(db,row,changed)
    if body['kind']=='reopen_actions':
        tasks,version=workspace(db,uid);selected={refs[i] for i in body['action_ids']}
        for task in tasks:
            if task['id'] in selected and task['status']=='completed':task.update(status='not_started',completedAt='')
        db.execute('UPDATE workspace SET value=?,version=? WHERE user_id=? AND name="tasks"',(json.dumps(tasks),version+1,uid))
    db.execute('UPDATE teaching_submission_sources SET review_pending=? WHERE user_id=? AND account=? AND input_id=?',
               (int(changed['observation']['review_pending']),uid,account,row['input_id']))
    db.execute('INSERT INTO submission_operations VALUES(?,?,?,?,?,?,?)',(uid,body['operation_id'],digest,rid,
       json.dumps(plan['receipt']),json.dumps(dict(plan['audit'],actor=uid)),datetime.now(timezone.utc).isoformat()))
    handler.reply(201,plan['receipt']);return True
