import json

from acme_app.infrastructure.redis_memory.client import get_redis_client


async def get_context(key: str) -> list[dict]:
    redis_client = get_redis_client()
    payload = await redis_client.get(key)
    return [] if payload is None else json.loads(payload)


async def set_context(key: str, value: list[dict], ttl_seconds: int = 1800) -> None:
    redis_client = get_redis_client()
    await redis_client.set(key, json.dumps(value), ex=ttl_seconds)
