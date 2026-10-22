"""Exact, server-owned text spans. Models select IDs; they cannot rewrite quotes."""
from copy import deepcopy
import re
from .adapters import AgentError


def catalog(record):
    spans = {}
    for message in [record] + record['context']:
        text = message['text']
        # Keep words/dates intact; fixed character cuts can split valid evidence.
        for match in re.finditer(r'[^。！？\n]+[。！？\n]*|[。！？\n]+', text):
            quote = match.group()
            if quote.strip():
                spans['q' + str(len(spans))] = dict(input_id=message['input_id'], quote=quote)
    return spans


def model_record(record):
    data = deepcopy(record)
    data.pop('text')
    for item in data['context']: item.pop('text')
    data['evidence_catalog'] = catalog(record)
    return data


def expand(envelope, spans):
    def walk(value):
        if isinstance(value, list): return [walk(item) for item in value]
        if not isinstance(value, dict): return value
        if 'span_id' in value:
            key = value['span_id']
            if (not isinstance(key, str) or key not in spans
                    or set(value) not in ({'span_id'}, {'span_id', 'value'}, {'span_id', 'kind', 'normalized'})):
                raise AgentError('invalid_agent_output', True, 'draft_evidence')
            return {**deepcopy(spans[key]), **{k: walk(v) for k, v in value.items() if k != 'span_id'}}
        return {k: walk(v) for k, v in value.items()}
    return walk(envelope)
