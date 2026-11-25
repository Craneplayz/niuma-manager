"""Refine a saved quick task without blocking its creation or adding another task."""
import json
import threading
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import task_operations
from text_jobs import connection

FIELDS = ('title', 'course', 'due', 'description')
MAX_WORKERS = 4
_lock = threading.Lock()
_workers = {}


def initialize(db, *, recover, resume_queued=True):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS quick_task_jobs(
            user_id TEXT NOT NULL, task_id TEXT NOT NULL, operation_id TEXT NOT NULL,
            state TEXT NOT NULL, request TEXT, applied_version INTEGER,
            PRIMARY KEY(user_id, task_id));
        CREATE INDEX IF NOT EXISTS quick_task_queue ON quick_task_jobs(state);
        CREATE INDEX IF NOT EXISTS quick_task_versions ON quick_task_jobs(user_id,applied_version);
    """)
    if recover:
        # A running model may already have been billed. End interrupted attempts
        # with the saved local task intact instead of submitting the input again.
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute('''SELECT * FROM quick_task_jobs WHERE state='running'
                                  OR (state='queued' AND ?=0)''', (resume_queued,)).fetchall():
            settle(db, row, None)
        return db.execute("SELECT 1 FROM quick_task_jobs WHERE state='queued' LIMIT 1").fetchone() is not None
    return False


def prepare(intent, task, ApiError):
    data = intent.get('quickRecognition')
    if (not isinstance(data, dict) or set(data) - {'localOnly'} != {'text','timezone','referenceDay','protectedFields'}
            or type(data.get('localOnly', False)) is not bool
            or intent.get('kind') != 'save' or 'expected' not in intent or intent['expected'] is not None
            or not isinstance(data['text'], str) or not data['text'].strip() or len(data['text']) > 4000):
        raise ApiError(400, '一句话识别只能用于刚添加的任务')
    protected = data['protectedFields']
    if (not isinstance(protected, list) or any(field not in FIELDS for field in protected)
            or len(protected) != len(set(protected))):
        raise ApiError(400, '手动设置的字段无效')
    try:
        if date.fromisoformat(data['referenceDay']).isoformat() != data['referenceDay']:
            raise ValueError()
        if not isinstance(data['timezone'], str) or len(data['timezone']) > 100:
            raise ValueError()
        ZoneInfo(data['timezone'])
        created_at = intent['createdAt']
        if datetime.fromisoformat(created_at.replace('Z','+00:00')).utcoffset() is None:
            raise ValueError()
    except (TypeError, ValueError, AttributeError, KeyError, ZoneInfoNotFoundError):
        raise ApiError(400, '一句话识别需要有效的本地日期、时区和创建时间') from None
    return dict(source=dict(text=data['text'], timezone=data['timezone'], referenceDay=data['referenceDay'],
                            createdAt=created_at),
                baseline={field: task.get(field) for field in FIELDS}, protectedFields=protected)


def observe_edit(db, uid, original, task):
    """Any user change locks that field for this job, even if later undone."""
    metadata = original.get('quickRecognition')
    if not isinstance(metadata, dict) or metadata.get('status') not in ('pending', 'done', 'failed'):
        return None
    info = dict(metadata)
    edited = {field for field in FIELDS if task.get(field) != original.get(field)}
    removed = task.get('recycleBin') or task.get('status') == 'cancelled'
    if info['status'] == 'pending':
        if removed:
            forget(db, uid, [task['id']])
            info['status'] = 'failed'
        elif edited:
            row = db.execute('SELECT request FROM quick_task_jobs WHERE user_id=? AND task_id=? AND request IS NOT NULL',
                             (uid, task['id'])).fetchone()
            if row:
                request = json.loads(row['request'])
                request['protectedFields'] = sorted(set(request['protectedFields']) | edited)
                db.execute('UPDATE quick_task_jobs SET request=? WHERE user_id=? AND task_id=?',
                           (json.dumps(request), uid, task['id']))
            else:
                info['status'] = 'failed'
    if edited and 'applied' in info:
        for key in ('applied', 'appliedFrom'):
            kept = {field: value for field, value in info[key].items() if field not in edited}
            if kept:
                info[key] = kept
            else:
                del info[key]
    return info


def before_write(db, uid, old, tasks, operation, ApiError):
    """Called under the task PUT's write transaction, including for legacy clients."""
    previous = {task['id']: task for task in old}
    intent = operation['intent'] if operation else {}
    quick_create = 'quickRecognition' in intent
    draft = intent.get('draft')
    if quick_create and (not isinstance(draft, dict) or not isinstance(draft.get('id'), str)
                         or not 1 <= len(draft['id']) <= 100):
        raise ApiError(400, '一句话识别需要有效的新任务编号')
    metadata_changed = False
    job_created = False
    matched = False
    for task in tasks:
        original = previous.pop(task['id'], None)
        info = observe_edit(db, uid, original, task) if original else None
        if quick_create and task['id'] == draft['id']:
            if original is not None:
                raise ApiError(409, '只能在新任务保存时启动识别')
            request = prepare(intent, task, ApiError)
            matched = True
            local = intent['quickRecognition'].get('localOnly', False)
            info = dict(text=request['source']['text'], status='done' if local else 'pending')
            if not local:
                db.execute("""INSERT INTO quick_task_jobs(user_id,task_id,operation_id,state,request)
                              VALUES(?,?,?,'queued',?)""",
                           (uid, task['id'], operation['id'], json.dumps(request)))
                job_created = True
        if task.get('quickRecognition') != info:
            metadata_changed = True
        if info is None:
            task.pop('quickRecognition', None)
        else:
            task['quickRecognition'] = info
    if quick_create and not matched:
        raise ApiError(400, '找不到要识别的新任务')
    forget(db, uid, previous)
    return job_created, metadata_changed or matched


def forget(db, uid, task_ids):
    db.executemany('DELETE FROM quick_task_jobs WHERE user_id=? AND task_id=?',
                   ((uid, task_id) for task_id in task_ids))


def check_version(db, uid, expected, current, ApiError):
    if type(expected) is not int or not 0 <= expected < current:
        return
    count = db.execute('''SELECT count(DISTINCT applied_version) FROM quick_task_jobs
        WHERE user_id=? AND applied_version>? AND applied_version<=?''', (uid, expected, current)).fetchone()[0]
    if count == current - expected:
        error = ApiError(409, 'AI 已更新任务，请读取新结果后保存')
        error.code = 'quick_recognition_changed'
        error.quick_version = current
        raise error


def settle(db, job, patch):
    """Workspace patch and terminal state commit together. None means failure."""
    uid, task_id, operation_id = job['user_id'], job['task_id'], job['operation_id']
    row = db.execute("""SELECT request FROM quick_task_jobs WHERE user_id=? AND task_id=? AND operation_id=?
                       AND state IN ('running','queued')""", (uid, task_id, operation_id)).fetchone()
    if row is None:
        return
    request = json.loads(row['request'])
    status = 'done' if patch is not None else 'failed'
    snapshot = task_operations.workspace(db, uid)
    task = next((item for item in snapshot['value'] if item['id'] == task_id), None)
    version = None
    if task and task.get('quickRecognition', {}).get('status') == 'pending':
        info = dict(text=request['source']['text'], status=status)
        before, after = {}, {}
        for field in FIELDS:
            if (patch and field in patch and field not in request['protectedFields']
                    and not task.get('recycleBin') and task.get('status') != 'cancelled'
                    and task.get(field) == request['baseline'][field] and task.get(field) != patch[field]):
                before[field], after[field] = task.get(field), patch[field]
        if after:
            task.update(after)
            info.update(appliedFrom=before, applied=after)
        task['quickRecognition'] = info
        version = snapshot['version'] + 1
        db.execute("UPDATE workspace SET value=?,version=? WHERE user_id=? AND name='tasks'",
                   (json.dumps(snapshot['value']), version, uid))
    db.execute("""UPDATE quick_task_jobs SET state=?,request=NULL,applied_version=?
                  WHERE user_id=? AND task_id=? AND operation_id=?""", (status, version, uid, task_id, operation_id))


def _work(path):
    try:
        while True:
            # Claim or retire while admission is locked. A concurrent create
            # either wakes another worker or is visible on the next iteration.
            with _lock, connection(path) as db:
                db.execute('BEGIN IMMEDIATE')
                job = db.execute("SELECT * FROM quick_task_jobs WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
                if job is None:
                    _workers[path].discard(threading.current_thread())
                    if not _workers[path]:
                        del _workers[path]
                    return
                db.execute("UPDATE quick_task_jobs SET state='running' WHERE user_id=? AND task_id=?",
                           (job['user_id'], job['task_id']))
            request = json.loads(job['request'])
            try:
                from quick_task_recognition import recognize_quick_task
                patch = recognize_quick_task(request['source'], request['baseline'])
            except Exception:
                patch = None
            with connection(path) as db:
                db.execute('BEGIN IMMEDIATE')
                settle(db, job, patch)
    finally:
        with _lock:
            workers = _workers.get(path)
            if workers:
                workers.discard(threading.current_thread())
                if not workers:
                    del _workers[path]


def wake(path):
    with _lock:
        workers = _workers.setdefault(path, set())
        if len(workers) < MAX_WORKERS:
            thread = threading.Thread(target=_work, args=(path,), daemon=True, name='quick-task-recognition')
            workers.add(thread)
            try:
                thread.start()
            except RuntimeError:
                workers.remove(thread)
                if not workers:
                    del _workers[path]
                    with connection(path) as db:
                        db.execute('BEGIN IMMEDIATE')
                        for job in db.execute("SELECT * FROM quick_task_jobs WHERE state='queued'").fetchall():
                            settle(db, job, None)
