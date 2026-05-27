"""Health + readiness routes.

/health is a liveness probe (always 200 if the process is up).
/ready checks each dependency in parallel; degraded dependencies show in the
response body but the endpoint still returns 200 so the container does not
flap on transient Redis/Keycloak hiccups. Docker compose's healthcheck-based
deps handle hard startup ordering instead.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.auth.keycloak_client import health as keycloak_health
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.db.session import get_db_session
from acme_app.infrastructure.mcp_client.client import MCPClient
from acme_app.infrastructure.redis_memory.client import ping_redis


router = APIRouter(tags=['health'])


@router.get('/health')
async def health() -> dict:
    return {'status': 'ok'}


@router.get('/ready')
async def ready(session: AsyncSession = Depends(get_db_session)) -> dict:
    mcp = MCPClient()
    db_ok, redis_ok, kc_ok, mcp_ok = await asyncio.gather(
        _safe(repo.ping_db(session)),
        _safe(ping_redis()),
        _safe(keycloak_health()),
        _safe(mcp.health()),
        return_exceptions=False,
    )
    return {
        'status': 'ok' if all([db_ok, mcp_ok]) else 'degraded',
        'dependencies': {
            'postgres': db_ok,
            'redis': redis_ok,
            'keycloak': kc_ok,
            'mcp_server': mcp_ok,
        },
    }


async def _safe(coro) -> bool:
    try:
        result = await coro
        return bool(result)
    except Exception:
        return False
