"""One account-bound transaction for ordinary tasks and pending new candidates."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

from jsonschema import ValidationError
from contracts import task_bulk as contract
import recognition
import submission_runtime
import task_operations
import usage


STATUS = {'not_started': 'todo', 'in_progress': 'doing', 'completed': 'done'}


def initialize(db):
    # Only fields required to reverse the action are retained, never task body text.
    db.execute('''CREATE TABLE IF NOT EXISTS task_bulk_undo(
        user_id TEXT NOT NULL, operation_id TEXT NOT NULL, generation INTEGER NOT NULL,
        inverse TEXT NOT NULL, PRIMARY KEY(user_id,operation_id))''')


def snapshot(task):
    due = task.get('due') or {}
    return dict(title=task['title'], notes=task.get('description') or '', course=task.get('course') or '',
                content_type=task.get('content_type') or 'task', due=due.get('date') or '',
                dueTime=due.get('time') or '', status=STATUS.get((task.get('recycleBin') or {}).get('status', task.get('status')), 'todo'),
                completedAt=task.get('completedAt') or '')


def candidate_snapshot(candidate):
    deadline = candidate.get('deadline') or {}
    day = clock = ''
    if deadline.get('kind') == 'deadline' and deadline.get('status') == 'explicit' and deadline.get('value'):
        if deadline.get('precision') == 'date':
            day = deadline['value']
        elif deadline.get('precision') == 'datetime':
            value = datetime.fromisoformat(deadline['value'].replace('Z', '+00:00'))
            local = value.astimezone(timezone(timedelta(hours=8)))
            day = local.date().isoformat()
            if value.second == 0 and value.microsecond == 0:
                clock = local.strftime('%H:%M')
    return dict(title=candidate['title'], notes=candidate.get('description') or '',
                course=candidate.get('course_or_project') or '', content_type=candidate.get('content_type') or 'task',
                due=day, dueTime=clock, status='todo', completedAt='')


def conflict(ApiError, message='所选内容已变化，请重新读取后核对；这批操作未保存'):
    raise ApiError(409, message)


def active(task):
    return task and not task.get('recycleBin') and task.get('status') != 'cancelled'


def due(day, clock):
    return dict(date=day, precision='datetime', time=clock) if day and clock else dict(date=day, precision='date') if day else None


def write(db, uid, tasks, version):
    db.execute('''INSERT INTO workspace VALUES(?,?,?,?) ON CONFLICT(user_id,name)
                  DO UPDATE SET value=excluded.value,version=excluded.version''',
               (uid, 'tasks', json.dumps(tasks), version))


def selected_candidate(db, uid, entry, ApiError):
    row = db.execute('SELECT * FROM recognition_candidates WHERE user_id=? AND id=?', (uid, entry['id'])).fetchone()
    if not row or row['state'] != 'pending' or row['version'] != entry['version']:
        conflict(ApiError)
    candidate = json.loads(row['candidate'])
    if candidate.get('notice_type') not in ('create', 'uncertain'):
        raise ApiError(400, '更新、延期和取消建议需要逐项核对目标，不能批量接受')
    batch = db.execute('SELECT demo FROM recognition_batches WHERE user_id=? AND request_id=?', (uid, row['request_id'])).fetchone()
    archived = db.execute('SELECT archived FROM recognition_import_records WHERE user_id=? AND request_id=?', (uid, row['request_id'])).fetchone()
    if not batch or batch['demo'] or archived and archived['archived']:
        conflict(ApiError)
    if candidate_snapshot(candidate) != entry['expected']:
        conflict(ApiError)
    return row


def task_inverse(task, changes):
    return dict(id=task['id'], expected=deepcopy(changes), previous={key: deepcopy(task.get(key)) for key in changes},
                absent=[key for key in changes if key not in task])


def apply_tasks(tasks, entries, intent, ApiError):
    action = intent['action']
    by_id = {task['id']: task for task in tasks}
    inverse = []
    for entry in entries:
        task = by_id.get(entry['id'])
        if not task or snapshot(task) != entry['expected']:
            conflict(ApiError)
        if action == 'restore':
            if not task.get('recycleBin'):
                conflict(ApiError)
        elif not active(task):
            conflict(ApiError)
        if action == 'complete' and task['status'] == 'completed':
            continue
        if action == 'reopen' and task['status'] != 'completed':
            continue
        if action in ('complete', 'reopen'):
            changes = dict(status='completed' if action == 'complete' else 'not_started',
                           completedAt=intent['completedAt'] if action == 'complete' else '')
        elif action == 'deadline':
            changes = dict(due=due(intent['date'], entry['expected']['dueTime']))
        elif action == 'trash':
            changes = dict(recycleBin=dict(deletedAt=intent['deletedAt'], status=task['status']),
                           status='completed' if task['status'] == 'completed' else 'cancelled')
        elif action == 'restore':
            changes = dict(recycleBin=None, status=task['recycleBin']['status'])
        else:
            raise ApiError(400, '批量操作类型无效')
        inverse.append(task_inverse(task, changes))
        task.update(changes)
        if changes.get('recycleBin', True) is None:
            task.pop('recycleBin', None)
    return inverse


def undo(db, uid, intent, tasks, ApiError):
    row = db.execute('SELECT * FROM task_bulk_undo WHERE user_id=? AND operation_id=?', (uid, intent['undoOf'])).fetchone()
    if not row or row['generation'] != task_operations.generation(db, uid):
        conflict(ApiError, '这次操作已无法撤销，请重新读取后核对')
    inverse = json.loads(row['inverse'])
    by_id = {task['id']: task for task in tasks}
    # Validate the entire reverse operation before applying any part.
    for entry in inverse['tasks']:
        task = by_id.get(entry['id'])
        if not task:
            conflict(ApiError)
        # A task moved into/out of the bin after the action must never be revived.
        if bool(task.get('recycleBin')) != bool(entry.get('recycleBin')):
            conflict(ApiError)
        if not task.get('recycleBin') and task.get('status') == 'cancelled':
            conflict(ApiError)
        for key, expected in entry['expected'].items():
            if task.get(key) != expected:
                conflict(ApiError)
    for entry in inverse['candidates']:
        row = db.execute('SELECT state,version FROM recognition_candidates WHERE user_id=? AND id=?', (uid, entry['id'])).fetchone()
        if not row or row['state'] != 'ignored' or row['version'] != entry['version']:
            conflict(ApiError)
    for entry in inverse['tasks']:
        task = by_id[entry['id']]
        for key, value in entry['previous'].items():
            if key in entry.get('absent', []):
                task.pop(key, None)
            else:
                task[key] = value
    # Reopen uses the shared transition that also persists submission pauses.
    for entry in inverse['candidates']:
        recognition.resolve_candidate(db, uid, entry['id'], dict(action='reopen', version=entry['version']), ApiError)


def apply(db, uid, body, ApiError):
    try:
        contract.check('request', body)
    except (ValueError, TypeError, ValidationError):
        raise ApiError(400, '批量操作格式不正确，请重新选择后重试') from None
    operation, intent = body['operation'], body['operation']['intent']
    db.execute('BEGIN IMMEDIATE')
    acknowledged = task_operations.acknowledged(db, uid, operation, ApiError)
    current = task_operations.workspace(db, uid)
    generation = task_operations.generation(db, uid)
    inverse_row = db.execute('SELECT generation FROM task_bulk_undo WHERE user_id=? AND operation_id=?', (uid, operation['id'])).fetchone()
    undo_intent = dict(kind='bulk', action='undo', undoOf=operation['id'], workspaceGeneration=generation)
    if acknowledged:
        return dict(current, operationId=operation['id'], replayed=True, batches=recognition.batches(db, uid),
                    candidates=task_operations.candidate_states(db, uid, [operation], current['value']), generation=generation,
                    undo=undo_intent if inverse_row and inverse_row['generation'] == generation else None)
    if body['version'] != current['version']:
        conflict(ApiError)
    tasks = deepcopy(current['value'])
    if intent['action'] == 'undo':
        undo(db, uid, intent, tasks, ApiError)
        undo_intent = None
    else:
        # Check every candidate and destination before any resolution side effects.
        existing_ids = {task['id'] for task in tasks}
        for entry in intent['candidates']:
            selected_candidate(db, uid, entry, ApiError)
            if entry['taskId'] in existing_ids:
                conflict(ApiError, '新增任务编号已存在，请重新选择后重试')
        inverse = dict(tasks=apply_tasks(tasks, intent['tasks'], intent, ApiError), candidates=[])
        # Shared candidate resolution checks versions, source policy and creates history.
        for entry in intent['candidates']:
            if intent['action'] == 'trash':
                resolution = dict(action='ignore', version=entry['version'])
                recognition.resolve_candidate(db, uid, entry['id'], resolution, ApiError)
                inverse['candidates'].append(dict(id=entry['id'], version=entry['version'] + 1))
            else:
                expected = entry['expected']
                new_due = due(intent['date'] if intent['action'] == 'deadline' else expected['due'], expected['dueTime'])
                resolution = dict(action='create', reviewed=True, target_id=None, version=entry['version'],
                                  tasks_version=task_operations.workspace(db, uid)['version'],
                                  draft=dict(title=expected['title'], description=expected['notes'], course=expected['course'] or None,
                                             content_type=expected['content_type'], due=new_due))
                if intent['action'] == 'complete':
                    resolution['complete'] = True
                review_intent = dict(action='create', expectedCandidateVersion=entry['version'], newTaskId=entry['taskId'])
                if intent['action'] == 'complete':
                    review_intent['completedAt'] = intent['completedAt']
                recognition.resolve_candidate(db, uid, entry['id'], resolution, ApiError, intent=review_intent, record_usage=False)
                created = next(task for task in task_operations.workspace(db, uid)['value'] if task['id'] == entry['taskId'])
                if intent['action'] == 'complete':
                    created['completedAt'] = intent['completedAt']
                    reverse = dict(id=created['id'], expected=dict(status='completed', completedAt=intent['completedAt']),
                                   previous=dict(status='not_started', completedAt=''))
                else:
                    reverse = dict(id=created['id'], expected=dict(due=new_due), previous=dict(due=due(expected['due'], expected['dueTime'])))
                tasks.append(created)
                inverse['tasks'].append(reverse)
        # Persist only minimal inverse fields, including the exact bin identity.
        by_id = {task['id']: task for task in tasks}
        for entry in inverse['tasks']:
            entry['recycleBin'] = deepcopy(by_id[entry['id']].get('recycleBin'))
        if inverse['tasks'] or inverse['candidates']:
            db.execute('INSERT INTO task_bulk_undo VALUES(?,?,?,?)', (uid, operation['id'], generation, json.dumps(inverse)))
        else:
            undo_intent = None
    submission_runtime.manual_task_write(db, uid, tasks)
    write(db, uid, tasks, current['version'] + 1)
    task_operations.record(db, uid, operation, current['version'] + 1, ApiError)
    usage.workspace_changes(db, uid, current['value'], tasks)
    return dict(value=tasks, version=current['version'] + 1, operationId=operation['id'], undo=undo_intent,
                batches=recognition.batches(db, uid),
                candidates=task_operations.candidate_states(db, uid, [operation], tasks), generation=generation)
