"""Backend planning transport; both modes use the existing planning 2.0 model."""
import json
import os
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ddl_text.codex_client import live_options
from task_planning import planning_harness

_ACTIVE = threading.Lock()
MAX_BYTES = 2_000_000


def plan(request, ApiError):
    """One planning call per backend process, with no implicit paid retries."""
    if not _ACTIVE.acquire(blocking=False):
        raise ApiError(503, '规划正在处理其他请求，请稍后重试')
    try:
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
            options = live_options(ROOT / '.env')
            # Keep the same provider requirement as the standalone planning service.
            if options['provider'] != 'qiniu' or not options['key'].strip():
                raise ValueError('Qiniu credential required')
        except (OSError, ValueError, TypeError, KeyError):
            raise ApiError(503, '规划配置不可用，请检查仓库根目录 .env 中的七牛模型配置与密钥') from None
        try:
            return planning_harness.generate(request, options)
        except Exception:
            raise ApiError(502, '规划模型不可用、超时或返回无效结果，请稍后重试；任务未修改') from None
    finally:
        _ACTIVE.release()
