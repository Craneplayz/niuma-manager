"""Account-bound receipts for durable browser writes. No operation content is retained."""
import hashlib
import json
import re
import time
from datetime import date, datetime


REVIEW_ACTIONS = ('create', 'update', 'postpone', 'cancel', 'ignore', 'reopen')
TARGET_FIELDS = ('title', 'description', 'course', 'content_type', 'due', 'status', 'completedAt')


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS task_write_receipts(
        user_id TEXT NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
        version INTEGER NOT NULL, created_at REAL NOT NULL,
        PRIMARY KEY(user_id, operation_id));
        CREATE TABLE IF NOT EXISTS task_write_epochs(user_id TEXT PRIMARY KEY, generation INTEGER NOT NULL);''')


def generation(db, uid):
    row = db.execute('SELECT generation FROM task_write_epochs WHERE user_id=?', (uid,)).fetchone()
    return row['generation'] if row else 0


def invalidate_pending(db, uid):
    db.execute('INSERT INTO task_write_epochs VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET generation=generation+1', (uid,))


def target_snapshot(task):
    return dict(title=task.get('title') or '', description=task.get('description') or '',
                course=task.get('course') or None, content_type=task.get('content_type') or 'task',
                due=task.get('due') or None, status=task.get('status') or 'not_started',
                completedAt=task.get('completedAt') or '')


def validate_review(intent, ApiError):
    allowed = {'kind', 'candidateId', 'action', 'expectedCandidateVersion', 'draft', 'targetId',
               'expectedTarget', 'newTaskId', 'complete', 'createdAt', 'completedAt',
               'previewSource', 'workspaceGeneration'}
    if set(intent) - allowed or not isinstance(intent.get('candidateId'), str) or not 1 <= len(intent['candidateId']) <= 500:
        raise ApiError(400, '候选操作标识格式不正确')
    action = intent.get('action')
    if action not in REVIEW_ACTIONS or type(intent.get('expectedCandidateVersion')) is not int or intent['expectedCandidateVersion'] < 1:
        raise ApiError(400, '候选操作版本或处理方式无效')
    if type(intent.get('complete', False)) is not bool or intent.get('complete') and action != 'create':
        raise ApiError(400, '完成操作需要用户主动确认新增任务')
    if action in ('create', 'update', 'postpone') and not isinstance(intent.get('draft'), dict):
        raise ApiError(400, '候选操作缺少确认内容')
    if action not in ('create', 'update', 'postpone') and intent.get('draft') is not None:
        raise ApiError(400, '此候选操作不能包含编辑内容')
    if action in ('update', 'postpone', 'cancel'):
        expected = intent.get('expectedTarget')
        if not isinstance(intent.get('targetId'), str) or not intent['targetId'] or not isinstance(expected, dict) or set(expected) != set(TARGET_FIELDS):
            raise ApiError(400, '候选变更缺少目标任务或编辑前值')
    elif intent.get('targetId') is not None or intent.get('expectedTarget') is not None:
        raise ApiError(400, '此候选操作不能指定已有目标任务')
    if action == 'create':
        if not isinstance(intent.get('newTaskId'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', intent['newTaskId']):
            raise ApiError(400, '新增任务编号无效')
    elif intent.get('newTaskId') is not None or intent.get('createdAt') is not None:
        raise ApiError(400, '只有新增候选可以指定任务编号或创建时间')
    if intent.get('createdAt') is not None:
        try:
            value = intent['createdAt']
            if not isinstance(value, str) or len(value) > 100 or datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset() is None:
                raise ValueError()
        except (TypeError, ValueError):
            raise ApiError(400, '候选创建时间无效') from None
    if intent.get('complete'):
        try:
            if not isinstance(intent.get('completedAt'), str) or date.fromisoformat(intent['completedAt']).isoformat() != intent['completedAt']:
                raise ValueError()
        except (TypeError, ValueError):
            raise ApiError(400, '候选完成日期无效') from None
    elif intent.get('completedAt') is not None:
        raise ApiError(400, '未完成操作不能指定完成日期')
    if 'previewSource' in intent and (not isinstance(intent['previewSource'], str) or len(intent['previewSource']) > 200):
        raise ApiError(400, '候选来源预览无效')


def identity(operation, uid, ApiError):
    if not isinstance(operation, dict) or set(operation) != {'id', 'owner', 'intent'}:
        raise ApiError(400, '任务保存操作标识格式不正确')
    if operation['owner'] != uid:
        raise ApiError(409, '账号已切换，请重新登录原账号恢复待保存操作')
    if not isinstance(operation['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', operation['id']):
        raise ApiError(400, '任务保存操作编号无效')
    intent = operation['intent']
    if not isinstance(intent, dict) or intent.get('kind') not in ('save', 'status', 'deadline', 'trash', 'restore', 'review'):
        raise ApiError(400, '任务保存操作类型无效')
    if type(intent.get('workspaceGeneration', 0)) is not int or intent.get('workspaceGeneration', 0) < 0:
        raise ApiError(400, '任务保存代次无效')
    if intent['kind'] == 'review':
        validate_review(intent, ApiError)
    encoded = json.dumps(intent, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    if len(encoded.encode()) > 100000:
        raise ApiError(400, '单条待保存操作过大')
    return operation['id'], hashlib.sha256(encoded.encode()).hexdigest()


def review_request(operation, uid, cid, body, ApiError):
    identity(operation, uid, ApiError)
    intent = operation['intent']
    if intent['kind'] != 'review' or intent['candidateId'] != cid:
        raise ApiError(400, '候选操作与请求目标不一致')
    allowed = {'operation', 'action', 'version', 'tasks_version', 'target_id', 'reviewed', 'draft', 'complete'}
    if (set(body) - allowed or body.get('action') != intent['action'] or
            type(body.get('version')) is not int or body['version'] != intent['expectedCandidateVersion'] or
            type(body.get('complete', False)) is not bool):
        raise ApiError(400, '候选操作与请求内容不一致')
    if (json.dumps(body.get('draft'),sort_keys=True) != json.dumps(intent.get('draft'),sort_keys=True) or
            body.get('target_id') != intent.get('targetId') or
            body.get('complete', False) != intent.get('complete', False)):
        raise ApiError(400, '候选操作与请求内容不一致')
    if intent['action'] not in ('ignore', 'reopen'):
        if body.get('reviewed') is not True:
            raise ApiError(400, '请先核对证据与变更')
        if type(body.get('tasks_version')) is not int or body['tasks_version'] < 0:
            raise ApiError(400, '候选操作任务版本无效')
    return intent


def acknowledged(db, uid, operation, ApiError):
    oid, fingerprint = identity(operation, uid, ApiError)
    row = db.execute('SELECT fingerprint FROM task_write_receipts WHERE user_id=? AND operation_id=?', (uid, oid)).fetchone()
    if row and row['fingerprint'] != fingerprint:
        raise ApiError(409, '操作编号已用于不同内容，请重新读取后核对')
    if not row and operation['intent'].get('workspaceGeneration', 0) != generation(db, uid):
        raise ApiError(409, '账号数据已清空，不能恢复清空前的操作；请重新读取')
    return bool(row)


def record(db, uid, operation, version, ApiError):
    oid, fingerprint = identity(operation, uid, ApiError)
    db.execute('INSERT INTO task_write_receipts VALUES(?,?,?,?,?)', (uid, oid, fingerprint, version, time.time()))


def workspace(db, uid):
    row = db.execute('SELECT value,version FROM workspace WHERE user_id=? AND name="tasks"', (uid,)).fetchone()
    return dict(value=json.loads(row['value']) if row else [], version=row['version'] if row else 0)


def candidate_states(db, uid, operations, tasks):
    ids = dict.fromkeys(operation['intent']['candidateId'] for operation in operations if operation['intent']['kind'] == 'review')
    task_ids = {event.get('storageId'): task['id'] for task in tasks
                for event in task.get('recognitionHistory', []) if isinstance(event, dict)}
    result = []
    for cid in ids:
        row = db.execute('SELECT state,version FROM recognition_candidates WHERE id=? AND user_id=?', (cid, uid)).fetchone()
        item = dict(id=cid, state=row['state'] if row else 'missing', version=row['version'] if row else 0)
        if row and row['state'] == 'applied' and cid in task_ids:
            item['taskId'] = task_ids[cid]
        result.append(item)
    return result


def snapshot(db, uid, operations):
    tasks = workspace(db, uid)
    return dict(tasks=tasks, candidates=candidate_states(db, uid, operations, tasks['value']), generation=generation(db, uid))


def reconcile(handler, db, uid, body, ApiError):
    if set(body) != {'owner', 'operations'}:
        raise ApiError(400, '待恢复操作请求格式不正确')
    if body.get('owner') != uid:
        raise ApiError(409, '账号已切换，请重新登录原账号恢复待保存操作')
    operations = body.get('operations')
    if not isinstance(operations, list) or len(operations) > 500:
        raise ApiError(400, '待恢复操作列表无效')
    # One read transaction pairs receipts with task and candidate versions.
    db.execute('BEGIN')
    applied = [operation['id'] for operation in operations if acknowledged(db, uid, operation, ApiError)]
    return handler.reply(200, dict(applied=applied, **snapshot(db, uid, operations)))
