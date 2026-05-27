"""Cache + Redis health utilities, kept separate from conversation memory."""
from __future__ import annotations

import json
from typing import Any

from acme_app.infrastructure.redis_memory.client import get_redis_client


CUSTOMER_LOOKUP_TTL = 15 * 60
TOOL_RESULT_TTL = 30 * 60


async def cache_customer_lookup(name: str, value: dict[str, Any]) -> None:
    await get_redis_client().set(f'customer_lookup:{name.lower()}', json.dumps(value), ex=CUSTOMER_LOOKUP_TTL)


async def get_customer_lookup(name: str) -> dict[str, Any] | None:
    raw = await get_redis_client().get(f'customer_lookup:{name.lower()}')
    return json.loads(raw) if raw else None


async def cache_tool_result(trace_ref: str, tool_name: str, value: dict[str, Any]) -> None:
    await get_redis_client().set(f'tool_result:{trace_ref}:{tool_name}', json.dumps(value), ex=TOOL_RESULT_TTL)


async def ping_redis() -> bool:
    try:
        return bool(await get_redis_client().ping())
    except Exception:
        return False
