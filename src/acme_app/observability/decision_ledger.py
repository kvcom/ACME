import time
from collections import defaultdict

TRACE_EVENTS: dict[str, list[dict]] = defaultdict(list)


def add_event(trace_ref: str, event_type: str, event_name: str, payload: dict, status: str = 'ok') -> None:
    TRACE_EVENTS[trace_ref].append({'event_type': event_type, 'event_name': event_name, 'payload': payload, 'status': status, 'created_at': int(time.time())})


def get_events(trace_ref: str) -> list[dict]:
    return TRACE_EVENTS.get(trace_ref, [])
