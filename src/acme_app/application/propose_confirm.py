"""Propose → Confirm → Create flow.

The agent never calls create_next_action directly. It stages a Proposed action
in Redis with an HMAC confirmation_token; the UI shows Confirm/Cancel; only
after Confirm does the API call into the MCP server.

If Redis is unavailable we keep an in-process fallback so eval and unit tests
work, but the demo path relies on Redis.
"""
from __future__ import annotations

import json
import time
from typing import Any

from acme_app.infrastructure.redis_memory.client import get_redis_client
from acme_app.policy.action_guard import idempotency_key, mint_confirmation_token


_IN_MEMORY_PENDING: dict[str, dict[str, Any]] = {}


def _key(conversation_ref: str) -> str:
    return f'pending_action:{conversation_ref}'


# Which MCP tool a confirmed proposal executes, by action_type. Anything not
# listed creates a new next_action (the default lifecycle).
#   UPDATE_ISSUE_STATUS  -> update the issues row status (support_user/admin)
#   UPDATE_NEXT_ACTION   -> complete/cancel an existing next_action (admin)
def target_tool_for(action_type: str) -> str:
    if action_type == 'UPDATE_ISSUE_STATUS':
        return 'update_issue_status'
    if action_type == 'UPDATE_NEXT_ACTION':
        return 'update_next_action'
    return 'create_next_action'


def build_proposed_action(
    *,
    trace_ref: str,
    customer_id: str | None,
    customer_name: str | None,
    issue_ref: str,
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    action_type = recommendation['action_type']
    target_tool = target_tool_for(action_type)
    action_ref = recommendation.get('action_ref', '')
    # The confirmation token is bound to the resource it acts on: the issue_ref
    # for creates and issue-status updates, the action_ref for action updates.
    # This stops a token minted for one resource being replayed against another.
    resource_ref = action_ref if target_tool == 'update_next_action' else issue_ref
    return {
        'trace_ref': trace_ref,
        'customer_id': customer_id,
        'customer_name': customer_name,
        'issue_ref': issue_ref,
        'action_type': action_type,
        'target_tool': target_tool,
        'new_status': recommendation.get('new_status', ''),
        'action_ref': action_ref,
        'title': recommendation.get('title', ''),
        'description': recommendation.get('description', ''),
        'priority': recommendation.get('priority', 'Medium'),
        'rationale': recommendation.get('rationale', ''),
        'evidence': recommendation.get('evidence', []),
        'due_at': recommendation.get('due_at'),
        'idempotency_key': idempotency_key(trace_ref, action_type, resource_ref),
        'confirmation_token': mint_confirmation_token(trace_ref, action_type, resource_ref),
        'expires_at': int(time.time()) + 600,
    }


def confirm_payload(pending: dict[str, Any], actor: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Build the (tool_name, payload) the confirm flow should execute for a
    pending proposal. Centralised so the API route and the orchestrator's
    confirm path stay in lock-step instead of both hard-coding one tool."""
    target = pending.get('target_tool') or target_tool_for(pending.get('action_type', ''))
    token = pending['confirmation_token']
    if target == 'update_issue_status':
        return target, {
            'actor': actor,
            'issue_ref': pending['issue_ref'],
            'new_status': pending.get('new_status') or 'Resolved',
            'confirmation_token': token,
        }
    if target == 'update_next_action':
        return target, {
            'actor': actor,
            'action_ref': pending.get('action_ref', ''),
            'new_status': pending.get('new_status') or 'Completed',
            'confirmation_token': token,
        }
    return 'create_next_action', {
        'actor': actor,
        'issue_ref': pending['issue_ref'],
        'action_type': pending['action_type'],
        'title': pending.get('title', ''),
        'description': pending.get('description', ''),
        'priority': pending.get('priority', 'Medium'),
        'due_at': pending.get('due_at'),
        'evidence': pending.get('evidence', []),
        'idempotency_key': pending['idempotency_key'],
        'confirmation_token': token,
    }


async def stage_pending_action(conversation_ref: str, action: dict[str, Any]) -> dict[str, Any]:
    try:
        await get_redis_client().set(_key(conversation_ref), json.dumps(action), ex=600)
    except Exception:
        _IN_MEMORY_PENDING[conversation_ref] = action
    return action


async def get_pending_action(conversation_ref: str) -> dict[str, Any] | None:
    try:
        raw = await get_redis_client().get(_key(conversation_ref))
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return _IN_MEMORY_PENDING.get(conversation_ref)


async def clear_pending_action(conversation_ref: str) -> None:
    try:
        await get_redis_client().delete(_key(conversation_ref))
    except Exception:
        pass
    _IN_MEMORY_PENDING.pop(conversation_ref, None)
