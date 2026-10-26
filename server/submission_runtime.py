"""Persistent account-scoped implementation of submission-operations/0.1."""
import hashlib
import json
import secrets
import re
from jsonschema import ValidationError
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, parse_qs, unquote

from contracts import submission_policy as policy, submission_operations as operations

BASES = {'submitted': 'review_submission_history_title', 'active': 'active_assignment_page', 'unknown': 'unknown'}
TEACHING_TIMEZONE = timezone(timedelta(hours=8))


class ActionLimitError(ValueError):
    """An incomplete action list must never authorize whole-source completion."""


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


def manually_controlled_candidates(db, uid, candidates):
    """Use durable operation targets, including explicit unknown/unchanged links.

    Exact candidate IDs scope the match even if a source control was replaced.
    Association changes intentionally do not mutate candidate state/version.
    """
    by_action={identity('candidate', c['id']):c['id'] for c in candidates}
    protected=set()
    if not by_action: return protected
    for row in db.execute("SELECT json_extract(audit,'$.operation') FROM submission_operations WHERE user_id=?",(uid,)):
        operation=json.loads(row[0])
        targets=([link['action_id'] for link in operation['links']] if operation['kind']=='associate_actions'
                 else operation['action_ids'])
        protected.update(by_action[aid] for aid in targets if aid in by_action)
        if len(protected)==len(by_action): break
    return protected


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
        # Only inherit when every contributing action has the same explicit
        # relationship. Dropping unknown/foreign history can mark an independent
        # action completed just because it was combined with a submission.
        inherited = [old.get(identity('candidate', h.get('storageId'))) for h in task.get('recognitionHistory', []) if isinstance(h,dict) and isinstance(h.get('storageId'),str)]
        if (not prior and inherited and all(a and a['relation'] != 'unknown' for a in inherited)
                and len({a['relation'] for a in inherited}) == 1):
            prior = dict(inherited[0], source_completion_paused=any(a['source_completion_paused'] for a in inherited))
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
        relation=prior['relation'] if prior else 'unknown'
        # Entering pending after a handled version restores the manual pause.
        # Once pending, respect the persisted flag: an explicit resume may have
        # cleared it. A later ignored -> pending transition pauses again.
        paused=relation=='assignment_submission' and (bool(prior and prior['source_completion_paused'])
            or c['state']=='pending' and c['version']>1 and (not prior or prior['decision']!='pending'))
        actions.append(dict(action_id=aid, kind='candidate', relation=relation,
            link_basis=prior['link_basis'] if prior else 'unresolved', decision='pending' if c['state']=='pending' else 'ignored',
            completion_origin=None, source_completion_paused=paused))
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
    if len(actions)>100: raise ActionLimitError('source action limit')
    if actions!=observation['actions'] or labels!=detail['action_labels']:
        observation['actions']=actions; detail['action_labels']=labels
        live = [a for a in actions if a['decision'] not in ('ignored','deleted')]
        if any(a['relation']=='unknown' for a in live): observation['scope']='unknown'
        elif any(a['relation']=='other' for a in live): observation['scope']='mixed'
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
    try: detail, refs=refresh(db,row)
    except ActionLimitError:
        detail=json.loads(row['detail']);detail['observation']['scope']='unknown'
        detail['observation']['control_revision']+=1;persist(db,row,detail)
        return  # Preserve all actions; do not complete a truncated selection.
    obs=detail['observation']; before=deepcopy(detail)
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
        task['completedAt']=now.astimezone(TEACHING_TIMEZONE).date().isoformat()
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


def refresh_request(db,uid,request_id,*,reopened_candidate_id=None):
    obs=control_for_request(db,uid,request_id)
    if obs:
        row=find(db,uid,obs['snapshot']['source_account_id'],obs['snapshot']['input_id'])
        try: detail,_=refresh(db,row)
        except ActionLimitError:
            detail=json.loads(row['detail']);detail['observation']['scope']='unknown'
            detail['observation']['control_revision']+=1;persist(db,row,detail)
        # An ignore need not have been observed by a read. Persist this explicit
        # transition even when the last saved decision was still pending.
        action=next((a for a in detail['observation']['actions']
            if a['action_id']==identity('candidate',reopened_candidate_id)),None) if reopened_candidate_id else None
        if (action and action['decision']=='pending' and action['relation']=='assignment_submission'
                and not action['source_completion_paused']):
            action['source_completion_paused']=True
            detail['observation']['control_revision']+=1;persist(db,row,detail)


def candidate_applied(db,uid,candidate_id,task_id):
    """Transfer controls once, in the candidate/task write transaction.

    An existing task's prior confirmation covers its old history only. Adding
    an independent/unknown/foreign action invalidates its submission mapping.
    Ordinary refreshes must not replay a consumed candidate's pause after an
    explicit resume on the task.
    """
    candidate_aid=identity('candidate',candidate_id);task_aid=identity('task',task_id)
    for row in db.execute('SELECT * FROM submission_controls WHERE user_id=?',(uid,)).fetchall():
        detail=json.loads(row['detail']);obs=detail['observation']
        actions={a['action_id']:a for a in obs['actions']}
        incoming,prior=actions.get(candidate_aid),actions.get(task_aid)
        if not incoming and not prior: continue
        if prior:
            before=deepcopy(prior)
            if incoming and incoming['relation']==prior['relation']:
                prior['source_completion_paused']=prior['source_completion_paused'] or incoming['source_completion_paused']
            else:
                prior.update(relation='unknown',link_basis='unresolved',source_completion_paused=False)
                obs['scope']='unknown'
            if prior!=before:
                obs['control_revision']+=1;persist(db,row,detail)
                row=dict(row,detail=json.dumps(detail))
        try: refresh(db,row)
        except ActionLimitError:
            # Preserve the transferred task protection even beyond the action
            # limit; a full refresh can become possible after later pruning.
            detail=json.loads(row['detail']);detail['observation']['scope']='unknown'
            detail['observation']['control_revision']+=1;persist(db,row,detail)


def manual_task_write(db,uid,tasks):
    old={t['id']:t for t in workspace(db,uid)[0]}
    reopened={identity('task',t['id']) for t in tasks if t['status']!='completed' and old.get(t['id'],{}).get('status')=='completed'}
    def history_ids(task):
        history=task.get('recognitionHistory')
        return {h['storageId'] for h in history if isinstance(h,dict) and isinstance(h.get('storageId'),str)} if isinstance(history,list) else set()
    remapped={identity('task',t['id']) for t in tasks if t['id'] in old
        and history_ids(t)!=history_ids(old[t['id']])}
    if not reopened and not remapped:return
    for row in db.execute('SELECT * FROM submission_controls WHERE user_id=?',(uid,)).fetchall():
        try: detail,_=refresh(db,row)
        except ActionLimitError:
            detail=json.loads(row['detail']);detail['observation']['scope']='unknown'
        obs=detail['observation'];changed=False
        for action in obs['actions']:
            if action['action_id'] in remapped:
                # A workspace history merge is not an explicit confirmation
                # that the newly combined task is still one submission action.
                action.update(relation='unknown',link_basis='unresolved',source_completion_paused=False)
                obs['scope']='unknown';changed=True
            if action['action_id'] in reopened and action['relation']=='assignment_submission':
                action.update(source_completion_paused=True,decision='confirmed',completion_origin=None);changed=True
        if changed:
            obs['control_revision']+=1;persist(db,row,detail)
            db.execute('INSERT INTO submission_events VALUES(?,?,?,?,?)',(secrets.token_hex(16),uid,row['record_id'],
                       json.dumps(dict(kind='manual_history_change' if remapped else 'manual_reopen',
                           action_ids=sorted(reopened|remapped))),datetime.now(timezone.utc).isoformat()))


def route(handler,db,uid,path,body,ApiError):
    root='/api/sources/submissions'
    if path!=root and not path.startswith(root+'/'):return False
    path=unquote(path)
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
            detail=json.loads(row['detail']);records.append({k:detail[k] for k in ('record_id','label','course_or_project')})
        handler.reply(200,dict(contract_version=operations.VERSION,source_account_id=account,records=records,next_cursor=rows[99]['record_id'] if len(rows)>100 else None));return True
    rid=path[len(root)+1:].split('/')[0]
    row=db.execute('SELECT * FROM submission_controls WHERE record_id=? AND user_id=? AND account=?',(rid,uid,account)).fetchone()
    if not row:raise ApiError(404,'作业记录不存在或来源已移除')
    try: detail,refs=refresh(db,row)
    except ActionLimitError: raise ApiError(503,'该来源关联超过 100 项，无法完整确认；请分别处理任务，不会自动完成。') from None
    if path==root+'/'+rid and handler.command=='GET':handler.reply(200,detail);return True
    if path==root+'/'+rid+'/source' and handler.command=='GET':
        from teaching import request_view
        args=parse_qs(urlsplit(handler.path).query,keep_blank_values=True)
        if 'expected_snapshot_id' in args or 'expected_control_revision' in args:
            obs=detail['observation']
            if (args.get('expected_snapshot_id') != [obs['snapshot']['snapshot_id']] or
                    args.get('expected_control_revision') != [str(obs['control_revision'])]):
                raise ApiError(409,'来源或操作版本已变化，请重新打开作业核对')
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
    if body['kind']=='associate_actions' and body['scope']=='submission_only':
        captured=db.execute('SELECT request FROM teaching_sources WHERE user_id=? AND account=? AND input_id=?',(uid,account,row['input_id'])).fetchone()
        records=json.loads(captured['request'])['records'] if captured else []
        if not records or any(e['record']['body_status']!='complete' or any(r['read_state']!='complete' for r in e['record']['references']) for e in records):
            raise ApiError(400,'当前来源未完整取得，请逐项关联；不能确认整份来源只有提交行动。')
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
