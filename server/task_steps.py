"""Persist checked-off work in an accepted decomposition."""
import json

import task_operations


def all_completed(task):
    steps = task.get('subtasks') or []
    return bool(steps) and all(isinstance(s, dict) and s.get('status') == 'completed' for s in steps)


def set_status(db, user_id, body, ApiError):
    task_id, step_id = body.get('taskId'), body.get('stepId')
    status, expected_version = body.get('status'), body.get('version')
    if (status not in ('pending', 'completed') or type(expected_version) is not int or expected_version < 0
            or not all(isinstance(value, str) and value for value in (task_id, step_id))):
        raise ApiError(400, '步骤或完成状态无效')
    db.execute('BEGIN IMMEDIATE')
    snapshot = task_operations.workspace(db, user_id)
    if snapshot['version'] != expected_version:
        raise ApiError(409, '任务已变化，请刷新后再更新步骤')
    task = next((t for t in snapshot['value'] if t['id'] == task_id and not t.get('recycleBin')), None)
    if not task or task.get('content_type') == 'notice':
        raise ApiError(404, '任务不存在')
    if task['status'] in ('completed', 'cancelled'):
        raise ApiError(409, '请先重新打开任务再更新步骤')
    steps = {s['id']: s for s in task.get('subtasks', []) if isinstance(s, dict) and isinstance(s.get('id'), str)}
    step = steps.get(step_id)
    if not step:
        raise ApiError(404, '步骤不存在，请重新打开任务')
    if step.get('status') == status:
        return snapshot
    if status == 'completed' and any(steps.get(dep, {}).get('status') != 'completed'
                                     for dep in step.get('dependsOn', [])):
        raise ApiError(409, '请先完成这个步骤依赖的前置步骤')
    if status == 'pending' and any(s.get('status') == 'completed' and step_id in s.get('dependsOn', [])
                                   for s in steps.values()):
        raise ApiError(409, '请先取消依赖此步骤的后续步骤')
    step['status'] = status
    snapshot['version'] += 1
    db.execute("UPDATE workspace SET value=?,version=? WHERE user_id=? AND name='tasks'",
               (json.dumps(snapshot['value']), snapshot['version'], user_id))
    return snapshot
