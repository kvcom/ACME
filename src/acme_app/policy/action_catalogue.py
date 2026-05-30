"""Action catalogue — live, DB-backed (D-019).

The Postgres `action_catalogue` table is the source of truth for which
action types this app recognises, who can propose them, what their required
fields are, and whether they need explicit confirmation.

A module-level mutable snapshot mirrors the DB into Python so hot-path
validation (planner, RBAC, adversarial check) doesn't pay a DB round-trip
per request. Two refresh paths keep it honest:

  1. `refresh_from_db()` called once during app startup, before the first
     request can land — guarantees the snapshot is current.
  2. `handle_event(event)` registered as a post-fan-out hook on the realtime
     broadcaster (D-018). When any `action_catalogue` row is INSERTed or
     UPDATEd, the snapshot reloads — adding a new action_type in the DB
     Explorer becomes effective within the same WebSocket round-trip,
     no service restart.

Public API (functions, not constants) so callers always read the *current*
state, never a stale import-time copy:

  - `allowed_action_types()      → frozenset[str]`
  - `validate_action_type(s)     → bool`
  - `get_definition(s)           → ActionDefinition | None`
  - `role_allowed(role, s)       → bool`
  - `required_fields(s)          → tuple[str, ...]`

Bootstrap policy: a hardcoded fallback of the eight seeded types loads at
import time so the app stays usable if the DB is briefly unreachable at
startup. `refresh_from_db()` overwrites the snapshot on first successful
load.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionDefinition:
    action_type: str
    label: str
    allowed_roles: tuple[str, ...]
    required_fields: tuple[str, ...]
    side_effect_level: str
    requires_confirmation: bool = True


# Bootstrap defaults — matches the seeded eight types. Replaced by the first
# successful `refresh_from_db()` call. Kept so the app degrades gracefully
# if the DB is unreachable at startup.
_BOOTSTRAP: dict[str, ActionDefinition] = {
    'ASSIGN_OWNER':         ActionDefinition('ASSIGN_OWNER',         'Assign Owner',             ('support_user', 'admin'), ('owner_name',),  'medium'),
    'REQUEST_MISSING_INFO': ActionDefinition('REQUEST_MISSING_INFO', 'Request Missing Info',     ('support_user', 'admin'), ('description',), 'low'),
    'CUSTOMER_FOLLOW_UP':   ActionDefinition('CUSTOMER_FOLLOW_UP',   'Customer Follow Up',       ('support_user', 'admin'), ('due_at',),      'low'),
    'ESCALATE_ISSUE':       ActionDefinition('ESCALATE_ISSUE',       'Escalate Issue',           ('support_user', 'admin'), ('issue_ref',),   'high'),
    'PREPARE_RECOVERY_PLAN':ActionDefinition('PREPARE_RECOVERY_PLAN','Prepare Recovery Plan',    ('support_user', 'admin'), ('due_at',),      'high'),
    'SCHEDULE_REVIEW':      ActionDefinition('SCHEDULE_REVIEW',      'Schedule Review',          ('support_user', 'admin'), ('due_at',),      'low'),
    'UPDATE_ISSUE_STATUS':  ActionDefinition('UPDATE_ISSUE_STATUS',  'Update Issue Status',      ('support_user', 'admin'), ('new_status',),  'medium'),
    'CREATE_EXEC_SUMMARY':  ActionDefinition('CREATE_EXEC_SUMMARY',  'Create Executive Summary', ('admin',),                ('description',), 'low'),
}

# Live snapshot. Initially the bootstrap defaults; replaced by `refresh_from_db`.
_snapshot: dict[str, ActionDefinition] = dict(_BOOTSTRAP)


# ── Public read API ─────────────────────────────────────────────────────────

def allowed_action_types() -> frozenset[str]:
    """All currently-active action types. Use this in `not in` checks."""
    return frozenset(_snapshot)


def validate_action_type(action_type: str) -> bool:
    return action_type in _snapshot


def get_definition(action_type: str) -> ActionDefinition | None:
    return _snapshot.get(action_type)


def role_allowed(role: str, action_type: str) -> bool:
    defn = _snapshot.get(action_type)
    return defn is not None and role in defn.allowed_roles


def required_fields(action_type: str) -> tuple[str, ...]:
    defn = _snapshot.get(action_type)
    return defn.required_fields if defn else ()


def snapshot() -> dict[str, ActionDefinition]:
    """Return a copy of the current snapshot. Useful for prompt-building."""
    return dict(_snapshot)


# ── Refresh paths ───────────────────────────────────────────────────────────

async def refresh_from_db() -> int:
    """Reload the snapshot from `action_catalogue WHERE is_active=true`.

    Returns the number of action types loaded. On any DB error, the
    existing snapshot is preserved and the error is logged — callers
    don't need to handle exceptions, since stale data is better than
    a broken app.
    """
    global _snapshot
    # Imported lazily so this module remains import-safe before the DB
    # session machinery is configured (e.g. in test collection).
    from acme_app.infrastructure.db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(text("""
                SELECT action_type, label, allowed_roles, required_fields,
                       side_effect_level, requires_confirmation
                FROM action_catalogue
                WHERE is_active = true
                ORDER BY action_type
            """))).all()
    except Exception as exc:
        _log.warning('action_catalogue refresh failed (%s); keeping previous snapshot',
                     type(exc).__name__)
        return len(_snapshot)

    new_snapshot: dict[str, ActionDefinition] = {}
    for r in rows:
        action_type, label, allowed_roles, required_fields_arr, side_effect_level, requires_confirmation = r
        new_snapshot[action_type] = ActionDefinition(
            action_type=action_type,
            label=label,
            allowed_roles=tuple(allowed_roles or ()),
            required_fields=tuple(required_fields_arr or ()),
            side_effect_level=side_effect_level,
            requires_confirmation=bool(requires_confirmation),
        )
    # Replace atomically — readers only ever see a fully-loaded snapshot.
    if new_snapshot:
        _snapshot = new_snapshot
        _log.info('action_catalogue loaded: %d active types', len(new_snapshot))
    else:
        _log.warning('action_catalogue refresh returned 0 rows; keeping previous snapshot')
    return len(_snapshot)


async def handle_event(event: dict[str, Any]) -> None:
    """Hook for the realtime broadcaster — refresh on any catalogue change."""
    if event.get('table') == 'action_catalogue':
        _log.info('action_catalogue realtime event %s/%s — reloading',
                  event.get('op'), event.get('id'))
        await refresh_from_db()
