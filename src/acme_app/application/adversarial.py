"""Adversarial input guard.

Three controls:
  1. Length bound — hard reject queries over 4096 chars.
  2. Pattern flag — regex-based suspicious-phrase detector. Flagged queries are
     logged and routed to a refusal narration (we do NOT silently drop, because
     legitimate users sometimes phrase things badly).
  3. Argument quarantine — exposed via validate_step_arguments, called by the
     orchestrator before any MCP call. Free-text from LLM plans must pass schema
     and allow-list checks before becoming tool input.
"""
from __future__ import annotations

import re
from typing import Any

from acme_app.infrastructure.mcp_client.schemas import ALLOWED_TOOLS
from acme_app.policy.action_catalogue import ALLOWED_ACTION_TYPES


MAX_QUERY_LENGTH = 4096

ADVERSARIAL_PATTERNS = [
    re.compile(r'ignore\s+(all\s+)?(previous|prior|above)\s+instructions?', re.I),
    re.compile(r'you\s+are\s+now\s+(an?\s+)?(admin|root|system|developer)', re.I),
    re.compile(r'system\s*:\s*you\s+are', re.I),
    re.compile(r'disregard\s+(your|the)\s+(rules|policy|guardrails)', re.I),
    re.compile(r'(?<!\w)pretend\s+to\s+be\s+(an?\s+)?(admin|root|system)', re.I),
]


def length_ok(query: str) -> bool:
    return len(query or '') <= MAX_QUERY_LENGTH


def pattern_flags(query: str) -> list[str]:
    return [pattern.pattern for pattern in ADVERSARIAL_PATTERNS if pattern.search(query or '')]


def check_query(query: str) -> tuple[bool, bool, list[str]]:
    """Return (length_ok, adversarial_detected, flags)."""
    if not length_ok(query):
        return False, True, ['query exceeds max length']
    flags = pattern_flags(query)
    return True, len(flags) > 0, flags


def validate_step(step_type: str, name: str) -> tuple[bool, str]:
    if step_type == 'tool' and name not in ALLOWED_TOOLS:
        return False, f'unknown tool: {name}'
    if step_type == 'skill' and name not in {'customer_escalation_summary', 'closure_readiness_check'}:
        return False, f'unknown skill: {name}'
    return True, 'ok'


def validate_step_arguments(name: str, args: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(args, dict):
        return False, 'arguments must be an object'
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 200:
            return False, f'argument {k} too long'
    if name == 'create_next_action':
        if args.get('action_type') and args['action_type'] not in ALLOWED_ACTION_TYPES:
            return False, f'action_type not in catalogue: {args.get("action_type")}'
    return True, 'ok'
