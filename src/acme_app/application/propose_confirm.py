import hashlib
import hmac
import os
import time

from acme_app.infrastructure.redis_memory.client import get_redis_client

_SECRET = os.getenv('CONFIRMATION_SECRET', 'acme-dev-secret').encode()
_IN_MEMORY_PENDING: dict[str, dict] = {}


def build_confirmation_token(trace_ref: str, action_type: str, issue_ref: str) -> str:
    raw = f'{trace_ref}|{action_type}|{issue_ref}'.encode()
    return hmac.new(_SECRET, raw, hashlib.sha256).hexdigest()


def build_idempotency_key(trace_ref: str, action_type: str, issue_ref: str) -> str:
    return hashlib.sha256(f'{trace_ref}|{action_type}|{issue_ref}'.encode()).hexdigest()


async def stage_pending_action(conversation_ref: str, payload: dict) -> dict:
    token = build_confirmation_token(payload['trace_ref'], payload['action_type'], payload['issue_ref'])
    data = payload | {'confirmation_token': token, 'expires_at': int(time.time()) + 600}
    try:
        redis_client = get_redis_client()
        await redis_client.set(f'conversation:{conversation_ref}:pending_action', str(data), ex=600)
    except Exception:
        # Eval/local fallback when Redis is unavailable.
        _IN_MEMORY_PENDING[conversation_ref] = data
    return data
