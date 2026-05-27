from acme_app.infrastructure.redis_memory.client import get_redis_client


async def ping_redis() -> bool:
    redis_client = get_redis_client()
    await redis_client.ping()
    return True
