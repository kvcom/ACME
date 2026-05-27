"""Redis-backed short-term memory for the agent.

Keys: conversation:{user}:{conv}:context (recent turns)
      conversation:{user}:{conv}:pending_action (proposed action JSON)
      conversation:{user}:{conv}:last_customer
"""
from __future__ import annotations

import json
from typing import Any

from acme_app.infrastructure.redis_memory.client import get_redis_client


CONTEXT_TTL = 30 * 60
PENDING_TTL = 10 * 60
LAST_REF_TTL = 30 * 60


def _ctx_key(user: str, conv: str) -> str:
    return f'conversation:{user}:{conv}:context'


def _pending_key(user: str, conv: str) -> str:
    return f'conversation:{user}:{conv}:pending_action'


def _last_customer_key(user: str, conv: str) -> str:
    return f'conversation:{user}:{conv}:last_customer'


def _last_issue_key(user: str, conv: str) -> str:
    return f'conversation:{user}:{conv}:last_issue'


async def get_context(user: str, conv: str) -> list[dict[str, Any]]:
    raw = await get_redis_client().get(_ctx_key(user, conv))
    return json.loads(raw) if raw else []


async def append_context(user: str, conv: str, turn: dict[str, Any]) -> None:
    history = await get_context(user, conv)
    history.append(turn)
    history = history[-20:]
    await get_redis_client().set(_ctx_key(user, conv), json.dumps(history), ex=CONTEXT_TTL)


async def set_pending_action(user: str, conv: str, action: dict[str, Any]) -> None:
    await get_redis_client().set(_pending_key(user, conv), json.dumps(action), ex=PENDING_TTL)


async def get_pending_action(user: str, conv: str) -> dict[str, Any] | None:
    raw = await get_redis_client().get(_pending_key(user, conv))
    return json.loads(raw) if raw else None


async def clear_pending_action(user: str, conv: str) -> None:
    await get_redis_client().delete(_pending_key(user, conv))


async def set_last_customer(user: str, conv: str, customer: dict[str, Any]) -> None:
    await get_redis_client().set(_last_customer_key(user, conv), json.dumps(customer), ex=LAST_REF_TTL)


async def get_last_customer(user: str, conv: str) -> dict[str, Any] | None:
    raw = await get_redis_client().get(_last_customer_key(user, conv))
    return json.loads(raw) if raw else None


async def set_last_issue(user: str, conv: str, issue_ref: str) -> None:
    await get_redis_client().set(_last_issue_key(user, conv), issue_ref, ex=LAST_REF_TTL)


async def get_last_issue(user: str, conv: str) -> str | None:
    return await get_redis_client().get(_last_issue_key(user, conv))
