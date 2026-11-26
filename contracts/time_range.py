"""Validate an optional end against the normalized start of a task interval."""
from datetime import date, datetime


def valid_range(deadline):
    if 'range_end' not in deadline:
        return True
    if deadline.get('kind') != 'reference' or deadline.get('status') != 'inferred':
        return False
    start, end = deadline.get('value'), deadline['range_end']
    try:
        if deadline['precision'] == 'date':
            return (len(start) == len(end) == 10 and date.fromisoformat(start) < date.fromisoformat(end))
        if deadline['precision'] != 'datetime':
            return False
        first, last = (datetime.fromisoformat(value.replace('Z', '+00:00')) for value in (start, end))
        return first.tzinfo is not None and last.tzinfo is not None and first < last
    except (TypeError, ValueError, AttributeError):
        return False
