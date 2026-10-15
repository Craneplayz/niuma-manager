"""Bounded semantic relation call; no credentials in inputs and no persistence."""
import json
from pathlib import Path
from jsonschema import ValidationError
from contracts import task_relations as contract
from .adapters import AgentError
from .extraction import _object
from .runner import run_attempt

SKILL = Path(__file__).parent / 'skills/task-relations/SKILL.md'


class RelationAdapter:
    def __init__(self, client):
        self.client = client

    def extract(self, request, settings, attempt):
        instructions = SKILL.read_text(encoding='utf-8') + '\nJSON Schema:\n' + json.dumps(contract.OUTPUT)
        payload=json.dumps(request,ensure_ascii=False)
        for repair in range(2):
            response = self.client.complete_json(instructions, payload, settings)
            try:
                if not isinstance(response, str) or len(response.encode('utf-8')) > contract.MAX_BYTES:
                    raise ValueError('invalid size')
                result = json.loads(response, object_pairs_hook=_object,
                                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
                contract.check_pair(request, result)
                return [result]
            except (ValueError, TypeError, KeyError, ValidationError):
                if repair:
                    raise AgentError('invalid_agent_output', False) from None
                # One correction shares the same disposable-process deadline.
                # Never 'fix' semantic relations in Python or expose private errors.
                payload=json.dumps(dict(context=request,correction_request=
                    '上次输出结构或引用不合法。重新检查：仅包含Schema字段，所有completion来源均在members和evidence；所有引用为对应节点连续原文。独立行动不要合并。请重新输出完整JSON。'),ensure_ascii=False)


def relate(nodes, *, client, settings):
    request = contract.context(nodes)
    # One disposable process bounds model latency; no unbounded correction loop.
    result = run_attempt(RelationAdapter(client), request, settings, 0)
    if len(result) != 1:
        raise AgentError('invalid_agent_output', False)
    try:
        return contract.check_pair(request, result[0])
    except (ValueError, TypeError, KeyError, ValidationError):
        raise AgentError('invalid_agent_output', False) from None
