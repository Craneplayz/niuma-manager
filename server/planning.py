"""Backend planning transport; both modes use the existing planning 2.0 model."""
import json
import os
if __package__:
    from .runtime_environment import model_env_path
    from .model_queue import ACTIVE as _ACTIVE, WAIT_ERROR
else:
    from runtime_environment import model_env_path
    from model_queue import ACTIVE as _ACTIVE, WAIT_ERROR
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ddl_text.codex_client import live_options
from task_planning import planning_harness
from task_planning.scheduling import rank_tasks

MAX_BYTES = 2_000_000


def priority_explanations(request, estimates):
    """Expose the existing scheduler's ranking factors without changing its decisions."""
    ranked, _ = rank_tasks(request, estimates)
    labels = ('截止前可用时间不足', '截止前余量极小', '截止前余量偏紧', '截止前余量较充足')
    return [dict(taskId=item['task']['id'], rank=index, level=item['level'],
                 availableMinutes=round(item['capacity']), estimatedMinutes=round(item['estimate']),
                 slackMinutes=round(item['slack']),
                 reason=(f"{labels[item['level']]}：截止前剩余可用约 {round(item['capacity'])} 分钟，"
                         f"预计还需 {round(item['estimate'])} 分钟；同等风险时先看截止时间，再看任务优先级。"))
            for index, item in enumerate(ranked, 1)]


def plan(request, ApiError):
    """Shared model admission, with no implicit paid retries."""
    if not _ACTIVE.acquire():
        raise ApiError(503, WAIT_ERROR)
    try:
        request['requestedAt'] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        endpoint = os.environ.get('HARNESS_URL')
        if endpoint:
            headers = {'Content-Type': 'application/json'}
            if os.environ.get('HARNESS_TOKEN'):
                headers['Authorization'] = 'Bearer ' + os.environ['HARNESS_TOKEN']
            try:
                with urlopen(Request(endpoint, json.dumps(request).encode(), headers), timeout=25) as response:
                    raw = response.read(MAX_BYTES + 1)
                    if len(raw) > MAX_BYTES:
                        raise ValueError('oversized response')
                    return json.loads(raw)
            except HTTPError as error:
                busy = error.code == 503
                error.close()
                if busy:
                    raise ApiError(503, '规划服务忙碌，请稍后重试') from None
                raise ApiError(502, '规划服务不可用或超时，请稍后重试；任务未修改') from None
            except Exception:
                raise ApiError(502, '规划服务不可用或超时，请稍后重试；任务未修改') from None
        try:
            options = live_options(model_env_path(ROOT))
            # Keep the same provider requirement as the standalone planning service.
            if options['provider'] != 'qiniu' or not options['key'].strip():
                raise ValueError('Qiniu credential required')
        except (OSError, ValueError, TypeError, KeyError):
            raise ApiError(503, '规划配置不可用，请检查当前环境的模型配置文件或环境变量中的七牛模型配置与密钥') from None
        try:
            return planning_harness.generate(request, options)
        except Exception:
            raise ApiError(502, '规划模型不可用、超时或返回无效结果，请稍后重试；任务未修改') from None
    finally:
        _ACTIVE.release()
