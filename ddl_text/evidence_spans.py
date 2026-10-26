"""Exact, server-owned text spans. Models select IDs; they cannot rewrite quotes."""
from copy import deepcopy
import re
from bisect import bisect_right, bisect_left
from .adapters import AgentError


# Evidence is copied into each task that cites it. Bound each quote before it
# enters the model so a short response cannot expand into megabytes of output.
MAX_SPAN_CHARS = 768
SPAN_OVERLAP_CHARS = 96
_UNITS = re.compile(r'[A-Za-z0-9_]+(?:[./:\-\'’][A-Za-z0-9_]+)*|\s+|.', re.DOTALL)


def bounded_quotes(text):
    if len(text) <= MAX_SPAN_CHARS:
        yield text
        return
    # Prefer complete words, ISO dates, times and identifiers. Exceptional
    # unbroken tokens still have a hard bound. Overlap preserves short phrases
    # (including localized dates) across a window boundary.
    boundaries = [0]
    for match in _UNITS.finditer(text):
        boundaries.extend(range(match.start() + MAX_SPAN_CHARS, match.end(), MAX_SPAN_CHARS))
        boundaries.append(match.end())
    start = 0
    while start < len(text):
        end_index = bisect_right(boundaries, start + MAX_SPAN_CHARS) - 1
        end = boundaries[end_index]
        yield text[start:end]
        if end == len(text): break
        overlap_index = bisect_left(boundaries, end - SPAN_OVERLAP_CHARS)
        start = boundaries[overlap_index] if boundaries[overlap_index] > start else end


def catalog(record):
    spans = {}
    for message in [record] + record['context']:
        text = message['text']
        # Preserve the existing short sentence IDs while bounding long English
        # paragraphs and text without punctuation/newlines as well.
        for match in re.finditer(r'[^。！？\n]+[。！？\n]*|[。！？\n]+', text):
            for quote in bounded_quotes(match.group()):
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
