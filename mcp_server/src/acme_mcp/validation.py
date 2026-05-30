"""Input sanitisation shared by MCP tools (D-019: live action catalogue).

The MCP server is a separate process from the main app — it has its own
sync psycopg connection and can't share the app's in-memory state. We
load the action catalogue from Postgres with a small TTL cache so the
catalogue is effectively live without paying a DB round-trip per tool
call.

The cache is invalidated by:
  - elapsed TTL (default 5 s), and
  - any tool call that itself writes to action_catalogue (none today,
    but the door is open if we add admin tools later).

Action-type allow-list AND the role→action mapping both come from the
DB now — `allowed_roles` is a TEXT[] column on the catalogue, so role
permissions reload along with the rest of the catalogue.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from acme_mcp.db import get_conn


_log = logging.getLogger(__name__)

# Static — not in the catalogue, these are higher-level domain values.
ALLOWED_PRIORITIES = frozenset({'Low', 'Medium', 'High', 'Critical'})
ALLOWED_ACTION_STATUSES = frozenset({'Proposed', 'Open', 'In Progress', 'Blocked', 'Completed', 'Cancelled'})
ALLOWED_ISSUE_STATUSES = frozenset({'Open', 'In Progress', 'Waiting for Customer', 'Escalated', 'Resolved', 'Closed'})

# Bootstrap fallback — used if the DB is unreachable at the moment of
# the first cache miss. Mirrors the seeded eight types.
_BOOTSTRAP_TYPES = frozenset({
    'ASSIGN_OWNER', 'REQUEST_MISSING_INFO', 'CUSTOMER_FOLLOW_UP', 'ESCALATE_ISSUE',
    'PREPARE_RECOVERY_PLAN', 'SCHEDULE_REVIEW', 'UPDATE_ISSUE_STATUS', 'CREATE_EXEC_SUMMARY',
})
_BOOTSTRAP_ROLE_PERMS = {
    'sales_user':   frozenset(),
    'support_user': frozenset({'ASSIGN_OWNER', 'REQUEST_MISSING_INFO', 'CUSTOMER_FOLLOW_UP', 'ESCALATE_ISSUE',
                                'PREPARE_RECOVERY_PLAN', 'SCHEDULE_REVIEW', 'UPDATE_ISSUE_STATUS'}),
    'admin':        _BOOTSTRAP_TYPES,
}


@dataclass
class _Snapshot:
    allowed_types: frozenset[str]
    role_perms: dict[str, frozenset[str]]
    loaded_at: float


_TTL_SECONDS = 5.0
_cache: _Snapshot | None = None


def _load() -> _Snapshot:
    """Synchronously read the live catalogue from Postgres."""
    types: set[str] = set()
    role_perms: dict[str, set[str]] = {}
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT action_type, allowed_roles
                    FROM action_catalogue
                    WHERE is_active = true
                """)
                for action_type, allowed_roles in cur.fetchall():
                    types.add(action_type)
                    for role in (allowed_roles or []):
                        role_perms.setdefault(role, set()).add(action_type)
    except Exception as exc:
        _log.warning('mcp action_catalogue load failed (%s); using bootstrap', type(exc).__name__)
        return _Snapshot(
            allowed_types=_BOOTSTRAP_TYPES,
            role_perms={r: frozenset(v) for r, v in _BOOTSTRAP_ROLE_PERMS.items()},
            loaded_at=time.time(),
        )
    if not types:
        # Empty result — treat as transient and keep bootstrap rather than
        # locking out every role (`role_may_create` would always say no).
        _log.warning('mcp action_catalogue load returned 0 active rows; using bootstrap')
        return _Snapshot(
            allowed_types=_BOOTSTRAP_TYPES,
            role_perms={r: frozenset(v) for r, v in _BOOTSTRAP_ROLE_PERMS.items()},
            loaded_at=time.time(),
        )
    return _Snapshot(
        allowed_types=frozenset(types),
        role_perms={r: frozenset(v) for r, v in role_perms.items()},
        loaded_at=time.time(),
    )


def _snapshot() -> _Snapshot:
    global _cache
    if _cache is None or (time.time() - _cache.loaded_at) > _TTL_SECONDS:
        _cache = _load()
    return _cache


def allowed_action_types() -> frozenset[str]:
    return _snapshot().allowed_types


def role_may_create(role: str, action_type: str) -> bool:
    return action_type in _snapshot().role_perms.get(role, frozenset())


def invalidate_cache() -> None:
    """Force the next call to re-read from DB. Currently unused; reserved
    for future admin tools that write to action_catalogue."""
    global _cache
    _cache = None
