"""MCP-side recommendation engine (D-020).

The MCP server is a separate process from the app; it has its own sync
psycopg connection and can't share the app's in-memory snapshot. Same
pattern as `validation.py` (D-019): load rules from Postgres on demand,
front with a 5 s TTL cache so we don't pay a DB round-trip per tool call.

Condition vocabulary mirrors `acme_app/policy/recommendation_engine.py`
exactly — bare value (equality), {"in":[...]}, {"not_in":[...]},
{"null":bool}, {"not_null":bool}. Empty conditions {} always match.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from acme_mcp.db import get_conn


_log = logging.getLogger(__name__)

_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class Rule:
    rule_ref: str
    recommender: str
    priority_order: int
    conditions: dict[str, Any]
    action_type: str
    recommended_priority: str
    rationale_template: str | None


@dataclass
class _Cache:
    rules: list[Rule]
    loaded_at: float


_cache: _Cache | None = None


def _condition_matches(condition: Any, actual: Any) -> bool:
    if isinstance(condition, dict):
        if 'in' in condition:
            return actual in (condition['in'] or [])
        if 'not_in' in condition:
            return actual not in (condition['not_in'] or [])
        if 'null' in condition:
            return (actual is None) == bool(condition['null'])
        if 'not_null' in condition:
            return (actual is not None) == bool(condition['not_null'])
        return False  # unknown operator → fail closed
    return actual == condition


def _all_conditions_match(conditions: dict[str, Any], facts: dict[str, Any]) -> bool:
    return all(_condition_matches(v, facts.get(k)) for k, v in conditions.items())


_TEMPLATE_RE = re.compile(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}')


def _render(template: str | None, facts: dict[str, Any]) -> str:
    if not template:
        return ''
    return _TEMPLATE_RE.sub(lambda m: '?' if facts.get(m.group(1)) is None else str(facts.get(m.group(1))), template)


def _load() -> list[Rule]:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT rule_ref, recommender, priority_order, conditions,
                       action_type, recommended_priority, rationale_template
                FROM action_recommendation_rules
                WHERE is_active = true
                ORDER BY recommender, priority_order
            """)
            return [Rule(
                rule_ref=r[0], recommender=r[1], priority_order=int(r[2]),
                conditions=r[3] if isinstance(r[3], dict) else {},
                action_type=r[4], recommended_priority=r[5], rationale_template=r[6],
            ) for r in cur.fetchall()]
    except Exception as exc:
        _log.warning('mcp recommendation_engine load failed (%s); returning empty', type(exc).__name__)
        return []


def _snapshot() -> list[Rule]:
    global _cache
    if _cache is None or (time.time() - _cache.loaded_at) > _TTL_SECONDS:
        _cache = _Cache(rules=_load(), loaded_at=time.time())
    return _cache.rules


def evaluate(recommender: str, facts: dict[str, Any]) -> dict[str, Any] | None:
    """Return {action_type, priority, rationale, matched_rule_ref} or None."""
    for rule in _snapshot():
        if rule.recommender != recommender:
            continue
        if _all_conditions_match(rule.conditions, facts):
            return {
                'action_type': rule.action_type,
                'priority': rule.recommended_priority,
                'rationale': _render(rule.rationale_template, facts),
                'matched_rule_ref': rule.rule_ref,
            }
    return None
