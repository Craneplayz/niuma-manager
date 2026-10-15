"""Account-scoped AI task decomposition preview and apply flow."""
import json
if __package__:
    from .runtime_environment import model_env_path
else:
    from runtime_environment import model_env_path
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.model_queue import ACTIVE as _ACTIVE, WAIT_ERROR
from ddl_text.adapters import AgentError
from ddl_text.codex_client import live_options
from task_planning.decomposition import generate_decomposition, validate_model_draft

TZ = timezone(timedelta(hours=8))


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS decomposition_proposals(
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, task_id TEXT NOT NULL,
        version INTEGER NOT NULL, result TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 0
    )''')


def _conversation(value, ApiError):
    if not isinstance(value, list) or len(value) > 20:
        raise ApiError(400, '拆解对话格式不正确')
    result = []
    for message in value:
        if (not isinstance(message, dict) or message.get('role') not in ['user', 'assistant']
                or not isinstance(message.get('content'), str)
                or not message['content'].strip() or len(message['content']) > 4000):
            raise ApiError(400, '拆解对话格式不正确')
        result.append(dict(role=message['role'], content=message['content'].strip()))
    return result


def _deadline(task):
    due = task.get('due')
    if not isinstance(due, dict) or not due.get('date'):
        return None
    clock = due.get('time') if due.get('precision') == 'datetime' else None
    return f"{due['date']}T{clock or '23:59:59'}+08:00"


def _request(task, conversation):
    progress = []
    for step in task.get('subtasks', []):
        if isinstance(step, dict) and isinstance(step.get('title'), str):
            progress.append(('已完成：' if step.get('status') == 'completed' else '未完成：') + step['title'])
    sources = []
    remaining_source_chars = 100_000
    for index, text in enumerate(task.get('sourceRequirements', [])):
        if isinstance(text, str) and text.strip() and remaining_source_chars:
            excerpt = text[:min(20000, remaining_source_chars)]
            sources.append(dict(id=f'requirement-{index + 1}', name='通知中的原始要求', text=excerpt))
            remaining_source_chars -= len(excerpt)
    return {
        'schema_version': '0.1-internal',
        'now': datetime.now(TZ).isoformat(),
        'timezone': 'Asia/Shanghai',
        'task': {
            'id': task['id'],
            'title': task['title'],
            'description': task.get('description') or None,
            'deadline': _deadline(task),
            'current_progress': '\n'.join(progress) or None,
            'user_scope': task.get('owner') or None,
            'source_materials': sources,
        },
        'conversation': conversation,
        'preferences': {
            'scope': '只拆解剩余工作，不安排具体日历时段；排程由独立的智能规划功能完成。'
        },
        'availability': {},
    }


def generate(task, conversation, ApiError):
    if not _ACTIVE.acquire():
        raise ApiError(503, WAIT_ERROR)
    try:
        try:
            options = live_options(model_env_path(ROOT))
            if not options['key'].strip():
                raise ValueError('missing credential')
        except (OSError, ValueError, TypeError, KeyError):
            raise ApiError(503, 'AI 拆解配置不可用，请检查模型配置与密钥') from None
        try:
            return generate_decomposition(_request(task, conversation), options)
        except AgentError as error:
            message = 'AI 拆解请求超时，请稍后重试' if error.code == 'agent_timeout' else 'AI 拆解模型暂时不可用，请稍后重试'
            raise ApiError(502, message) from None
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ApiError(502, '模型返回的拆解结果不符合要求，请重试') from None
    finally:
        _ACTIVE.release()


def route(handler, db, user_id, path, body, ApiError):
    if path == '/api/decomposition/proposals' and handler.command == 'POST':
        task_id = body.get('taskId')
        if not isinstance(task_id, str) or not task_id:
            raise ApiError(400, '请选择需要拆解的任务')
        conversation = _conversation(body.get('conversation', []), ApiError)
        row = db.execute('SELECT value,version FROM workspace WHERE user_id=? AND name="tasks"', (user_id,)).fetchone()
        tasks = json.loads(row['value']) if row else []
        task = next((item for item in tasks if item.get('id') == task_id), None)
        if not task:
            raise ApiError(404, '任务不存在')
        if task.get('status') in ['completed', 'cancelled']:
            raise ApiError(400, '已完成或已取消的任务无需拆解')
        result = generate(task, conversation, ApiError)
        if result['status'] == 'ready_to_plan':
            request_id = secrets.token_hex(16)
            db.execute('INSERT INTO decomposition_proposals VALUES(?,?,?,?,?,0)',
                       (request_id, user_id, task_id, row['version'], json.dumps(result)))
            result = dict(result, requestId=request_id)
        response = {key: result[key] for key in ['status', 'assessment', 'questions', 'plan']}
        response.update(contract_version='0.1', taskId=task_id)
        if 'requestId' in result:
            response['requestId'] = result['requestId']
        return handler.reply(200, response) or True

    if path == '/api/decomposition/apply' and handler.command == 'POST':
        request_id = body.get('requestId')
        if not isinstance(request_id, str) or not request_id:
            raise ApiError(400, '拆解方案编号不正确')
        task_id = body.get('taskId')
        if not isinstance(task_id, str) or not task_id:
            raise ApiError(400, '请选择需要拆解的任务')
        db.execute('BEGIN IMMEDIATE')
        proposal = db.execute('SELECT * FROM decomposition_proposals WHERE id=? AND user_id=?',
                              (request_id, user_id)).fetchone()
        row = db.execute('SELECT value,version FROM workspace WHERE user_id=? AND name="tasks"',
                         (user_id,)).fetchone()
        if not proposal or not row:
            raise ApiError(404, '拆解方案不存在')
        if proposal['task_id'] != task_id:
            raise ApiError(409, '拆解方案与当前任务不匹配')
        if proposal['applied']:
            raise ApiError(409, '拆解方案已经应用')
        if row['version'] != proposal['version']:
            raise ApiError(409, '任务已经变化，请重新拆解')
        tasks = json.loads(row['value'])
        handler.usage_tasks = json.loads(row['value'])
        task = next((item for item in tasks if item.get('id') == proposal['task_id']), None)
        if not task:
            raise ApiError(404, '任务不存在')
        result = json.loads(proposal['result'])
        validate_model_draft(result)
        items = result['plan']['subtasks']
        identifiers = {item['id']: secrets.token_hex(16) for item in items}
        task['subtasks'] = [{
            'id': identifiers[item['id']],
            'title': item['title'],
            'status': 'pending',
            'outcome': item['outcome'],
            'estimatedMinutes': item['duration_minutes'],
            'dependsOn': [identifiers[value] for value in item['depends_on']],
            'sourceBasis': item['source_basis'],
            'assumption': item['assumption'],
        } for item in items]
        task['estimatedMinutes'] = sum(item['duration_minutes'] for item in items)
        db.execute('UPDATE workspace SET value=?,version=version+1 WHERE user_id=? AND name="tasks"',
                   (json.dumps(tasks), user_id))
        db.execute('UPDATE decomposition_proposals SET applied=1 WHERE id=?', (request_id,))
        return handler.reply(200, dict(value=tasks, version=row['version'] + 1)) or True
    return False
