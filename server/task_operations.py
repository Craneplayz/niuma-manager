"""Account-bound receipts for durable browser task writes. No task content is retained."""
import hashlib
import json
import re
import time


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


def identity(operation, uid, ApiError):
    if not isinstance(operation, dict) or set(operation) != {'id', 'owner', 'intent'}:
        raise ApiError(400, '任务保存操作标识格式不正确')
    if operation['owner'] != uid:
        raise ApiError(409, '账号已切换，请重新登录原账号恢复待保存操作')
    if not isinstance(operation['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', operation['id']):
        raise ApiError(400, '任务保存操作编号无效')
    intent = operation['intent']
    if not isinstance(intent, dict) or intent.get('kind') not in ('save', 'status', 'deadline', 'trash', 'restore'):
        raise ApiError(400, '任务保存操作类型无效')
    encoded = json.dumps(intent, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    if len(encoded.encode()) > 100000:
        raise ApiError(400, '单条待保存操作过大')
    return operation['id'], hashlib.sha256(encoded.encode()).hexdigest()


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


def reconcile(handler, db, uid, body, ApiError):
    if body.get('owner') != uid:
        raise ApiError(409, '账号已切换，请重新登录原账号恢复待保存操作')
    operations = body.get('operations')
    if not isinstance(operations, list) or len(operations) > 500:
        raise ApiError(400, '待恢复操作列表无效')
    # One read transaction pairs the receipts with the exact matching task/version snapshot.
    db.execute('BEGIN')
    applied = [operation['id'] for operation in operations if acknowledged(db, uid, operation, ApiError)]
    return handler.reply(200, dict(applied=applied, tasks=workspace(db, uid), generation=generation(db, uid)))
