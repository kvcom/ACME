from __future__ import annotations

import redis.asyncio as redis

from acme_app.config import settings

_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def ping_redis() -> bool:
    try:
        return bool(await get_redis_client().ping())
    except Exception:
        return False
