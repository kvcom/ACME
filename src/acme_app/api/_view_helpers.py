"""Small presentation helpers shared by the routes.

Keeps Jinja templates dumb — pre-compute badge_class, when, and grouped
buckets here rather than putting business logic in the template.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_BADGE_CLASS = {
    'Grounded': 'grounded',
    'Partially Grounded': 'partial',
    'Needs Review': 'needsreview',
    'Permission Denied': 'denied',
    'Action Proposed': 'proposed',
    'Confirmation Required': 'proposed',
    'Action Created': 'created',
    'Action Cancelled': 'cancelled',
    'Insufficient Evidence': 'insufficient',
    'Clarification Required': 'clarify',
    'Adversarial Input Blocked': 'adversarial',
    'LLM Unavailable': 'needsreview',
    'Resolution Required': 'needsreview',
}


def badge_class_for(status: str | None) -> str:
    return _BADGE_CLASS.get(status or '', 'neutral')


def relative_when(iso: str | None) -> str:
    if not iso:
        return '—'
    try:
        dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(tz=dt.tzinfo)
    delta = now - dt
    if delta.days >= 7:
        return dt.strftime('%a')
    if delta.days >= 1:
        return f'{delta.days}d'
    hours = delta.seconds // 3600
    if hours >= 1:
        return f'{hours}h'
    minutes = max(1, delta.seconds // 60)
    return f'{minutes}m'


def day_bucket(iso: str | None) -> str:
    if not iso:
        return 'Earlier'
    try:
        dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except ValueError:
        return 'Earlier'
    today = datetime.now(tz=dt.tzinfo or timezone.utc).date()
    diff = (today - dt.date()).days
    if diff <= 0:
        return 'Today'
    if diff == 1:
        return 'Yesterday'
    return 'Earlier'


def enrich_trace_row(row: dict[str, Any]) -> dict[str, Any]:
    row['badge_class'] = badge_class_for(row.get('status'))
    row['when'] = relative_when(row.get('created_at'))
    return row


def enrich_conversation_row(row: dict[str, Any]) -> dict[str, Any]:
    row['when'] = relative_when(row.get('last_message_at'))
    row['bucket'] = day_bucket(row.get('last_message_at'))
    row.setdefault('pending', False)
    row.setdefault('badge', None)
    row.setdefault('badge_class', 'neutral')
    return row


def group_conversations(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    enriched = [enrich_conversation_row(r) for r in rows]
    out: dict[str, list[dict[str, Any]]] = {'Today': [], 'Yesterday': [], 'Earlier': []}
    for row in enriched:
        out[row['bucket']].append(row)
    return [(name, items) for name, items in out.items() if items]
