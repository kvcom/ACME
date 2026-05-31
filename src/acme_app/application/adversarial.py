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
from acme_app.policy import action_catalogue


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


def pattern_matches(query: str) -> list[dict[str, str]]:
    """Human-readable rule matches for trace explanations."""
    matches: list[dict[str, str]] = []
    for pattern in ADVERSARIAL_PATTERNS:
        match = pattern.search(query or '')
        if match:
            matches.append({
                'rule': pattern.pattern,
                'matched_text': match.group(0),
            })
    return matches


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


_ISSUE_REF_RE = re.compile(r'^ISS-\d{3,5}$', re.I)

# Per-tool required-argument contracts. The orchestrator skips a plan step
# whose arguments don't satisfy these, so a confused LLM never reaches the
# MCP server with garbage and produces a visible 400 in the trace.
_TOOL_REQUIRED_ARGS: dict[str, tuple[str, ...]] = {
    'search_customers':        ('customer_name',),
    'get_customer_profile':    ('customer_name',),
    'get_open_issues':         ('customer_name',),   # also accepts customer_id
    'summarise_issue_history': ('issue_ref',),
    'recommend_next_action':   ('issue_ref',),
}
_SKILL_REQUIRED_ARGS: dict[str, tuple[str, ...]] = {
    'customer_escalation_summary': ('customer_name',),
    'closure_readiness_check':     ('issue_ref',),
}


def validate_step_arguments(name: str, args: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(args, dict):
        return False, 'arguments must be an object'
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 200:
            return False, f'argument {k} too long'
    if name == 'create_next_action':
        if args.get('action_type') and args['action_type'] not in action_catalogue.allowed_action_types():
            return False, f'action_type not in catalogue: {args.get("action_type")}'
        return True, 'ok'

    # Required-field gate: smaller local LLMs sometimes invoke tools with the
    # arguments of an entirely different tool (e.g. summarise_issue_history with
    # customer_name). Catch that here, not at the MCP layer.
    required = _TOOL_REQUIRED_ARGS.get(name) or _SKILL_REQUIRED_ARGS.get(name)
    if required:
        # get_open_issues uniquely accepts either customer_name OR customer_id.
        if name == 'get_open_issues':
            if not (args.get('customer_name') or args.get('customer_id')):
                return False, 'get_open_issues requires customer_name or customer_id'
        else:
            missing = [k for k in required if not args.get(k)]
            if missing:
                return False, f'{name} missing required argument(s): {", ".join(missing)}'

    # Shape check for issue_ref: must look like ISS-NNN.
    issue_ref = args.get('issue_ref')
    if issue_ref and not _ISSUE_REF_RE.match(str(issue_ref)):
        return False, f'issue_ref must look like ISS-NNN (got "{issue_ref}")'

    return True, 'ok'
