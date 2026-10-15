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
        response = self.client.complete_json(instructions, json.dumps(request, ensure_ascii=False), settings)
        try:
            if not isinstance(response, str) or len(response.encode('utf-8')) > contract.MAX_BYTES:
                raise ValueError('invalid size')
            result = json.loads(response, object_pairs_hook=_object,
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
            contract.check_pair(request, result)
        except (ValueError, TypeError, KeyError, ValidationError):
            raise AgentError('invalid_agent_output', False) from None
        return [result]


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
