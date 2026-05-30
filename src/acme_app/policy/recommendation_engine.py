"""Action recommendation engine — DB-driven (D-020).

Each recommender (the `recommend_next_action` MCP tool, the
`customer_escalation_summary` skill, the `closure_readiness_check` skill)
now consults this engine instead of running a hand-coded if/elif chain.
Rules live in `action_recommendation_rules`; the engine matches them
against an in-memory facts dict and returns the first match.

Operators add a new recommendation by INSERTing a row — no code change.

Live-snapshot pattern is the same as D-019's action catalogue:
  - Bootstrap with an empty snapshot at import time.
  - `refresh_from_db()` populates it at app startup.
  - `handle_event()` registered as a post-fan-out hook on the realtime
    broadcaster reloads on any `action_recommendation_rules` change
    within ~2 ms of the DB write.

Condition vocabulary (JSONB on `conditions`):
  - Bare value:      `{"severity": "P1"}`       — equality
  - In list:         `{"tier": {"in": ["Enterprise","Strategic"]}}`
  - Not in list:     `{"sla":  {"not_in": ["Within SLA"]}}`
  - Null/not-null:   `{"owner": {"null": true}}` / `{"owner": {"not_null": true}}`
  - Empty dict `{}`: always matches (catch-all rule, use last)

First-match-wins on `priority_order ASC` (lower = higher priority).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    rule_ref: str
    recommender: str
    priority_order: int
    conditions: dict[str, Any]
    action_type: str
    recommended_priority: str
    rationale_template: str | None


# Bootstrap snapshot is empty: the engine returns None until refresh_from_db()
# populates it. Callers MUST handle None — typically by falling back to a
# safe default (e.g. SCHEDULE_REVIEW/Low) so a transient DB outage doesn't
# crash the agent.
_rules: list[Rule] = []


def _coerce_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _condition_matches(condition: Any, actual: Any) -> bool:
    """Evaluate one condition against the actual fact value."""
    if isinstance(condition, dict):
        if 'in' in condition:
            return actual in (condition['in'] or [])
        if 'not_in' in condition:
            return actual not in (condition['not_in'] or [])
        if 'null' in condition:
            want_null = bool(condition['null'])
            return (actual is None) == want_null
        if 'not_null' in condition:
            want_not_null = bool(condition['not_null'])
            return (actual is not None) == want_not_null
        # Unknown operator — fail closed so a typo in conditions doesn't
        # silently match everything.
        return False
    return actual == condition


def _all_conditions_match(conditions: dict[str, Any], facts: dict[str, Any]) -> bool:
    for key, expected in conditions.items():
        if not _condition_matches(expected, facts.get(key)):
            return False
    return True


_TEMPLATE_RE = re.compile(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}')


def _render_rationale(template: str | None, facts: dict[str, Any]) -> str:
    """Render a `{var}` template against facts. Missing vars render as '?'.
    A None or empty template returns '' so callers can supply their own."""
    if not template:
        return ''
    def replace(m):
        key = m.group(1)
        value = facts.get(key)
        return '?' if value is None else str(value)
    return _TEMPLATE_RE.sub(replace, template)


@dataclass(frozen=True)
class Recommendation:
    action_type: str
    priority: str
    rationale: str
    matched_rule_ref: str


def evaluate(recommender: str, facts: dict[str, Any]) -> Recommendation | None:
    """Return the first matching rule's recommendation for `recommender`,
    or None if no rule matches (or the snapshot is empty)."""
    for rule in _rules:
        if rule.recommender != recommender:
            continue
        if _all_conditions_match(rule.conditions, facts):
            return Recommendation(
                action_type=rule.action_type,
                priority=rule.recommended_priority,
                rationale=_render_rationale(rule.rationale_template, facts),
                matched_rule_ref=rule.rule_ref,
            )
    return None


def snapshot_for(recommender: str) -> list[Rule]:
    """Return rules for a specific recommender, sorted (debugging / UI)."""
    return [r for r in _rules if r.recommender == recommender]


async def refresh_from_db() -> int:
    """Reload all active rules from `action_recommendation_rules`.

    On any DB error the previous snapshot is preserved — stale rules are
    better than a broken agent. Returns the count of loaded rules.
    """
    global _rules
    from acme_app.infrastructure.db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(text("""
                SELECT rule_ref, recommender, priority_order, conditions,
                       action_type, recommended_priority, rationale_template
                FROM action_recommendation_rules
                WHERE is_active = true
                ORDER BY recommender, priority_order
            """))).all()
    except Exception as exc:
        _log.warning('recommendation_engine refresh failed (%s); keeping previous snapshot',
                     type(exc).__name__)
        return len(_rules)

    new_rules: list[Rule] = []
    for r in rows:
        rule_ref, recommender, priority_order, conditions, action_type, recommended_priority, rationale_template = r
        new_rules.append(Rule(
            rule_ref=rule_ref,
            recommender=recommender,
            priority_order=int(priority_order),
            conditions=_coerce_dict(conditions),
            action_type=action_type,
            recommended_priority=recommended_priority,
            rationale_template=rationale_template,
        ))
    _rules = new_rules
    _log.info('recommendation_engine loaded: %d active rules', len(new_rules))
    return len(new_rules)


async def handle_event(event: dict[str, Any]) -> None:
    """Realtime hook — reload whenever the rules table changes."""
    if event.get('table') == 'action_recommendation_rules':
        _log.info('recommendation_engine realtime event %s/%s — reloading',
                  event.get('op'), event.get('id'))
        await refresh_from_db()
