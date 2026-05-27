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


def build_proposed_action(
    *,
    trace_ref: str,
    customer_id: str | None,
    customer_name: str | None,
    issue_ref: str,
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    action_type = recommendation['action_type']
    return {
        'trace_ref': trace_ref,
        'customer_id': customer_id,
        'customer_name': customer_name,
        'issue_ref': issue_ref,
        'action_type': action_type,
        'title': recommendation.get('title', ''),
        'description': recommendation.get('description', ''),
        'priority': recommendation.get('priority', 'Medium'),
        'rationale': recommendation.get('rationale', ''),
        'evidence': recommendation.get('evidence', []),
        'due_at': recommendation.get('due_at'),
        'idempotency_key': idempotency_key(trace_ref, action_type, issue_ref),
        'confirmation_token': mint_confirmation_token(trace_ref, action_type, issue_ref),
        'expires_at': int(time.time()) + 600,
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
