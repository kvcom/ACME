"""Chat routes: POST /chat (non-streaming) and GET /chat/stream (SSE).

Both go through the same orchestrator. The SSE variant uses an event sink to
stream progress; the POST variant ignores the sink and returns the final
ChatResponse JSON.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from acme_app.api._view_helpers import badge_class_for, group_conversations
from acme_app.application.orchestrator import run_agent
from acme_app.application.propose_confirm import get_pending_action, stage_pending_action
from acme_app.application.schemas import ChatResponse
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.config import settings
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.db.session import get_db_session
from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY, default_key as registry_default_key, visible_registry
from acme_app.policy.action_guard import mint_confirmation_token

router = APIRouter(prefix='/chat', tags=['chat'])


class ChatInput(BaseModel):
    query: str
    conversation_ref: str = 'CONV-DEMO'
    model_key: str | None = None
    # Back-compat: the old `provider` field still works.
    provider: str | None = None
    resolution_route: str | None = None


def _resolve_model(model_key: str | None, provider: str | None) -> str:
    """Pick a model_key from the request, falling back to provider or default."""
    if model_key and model_key in MODEL_REGISTRY:
        return model_key
    if provider:
        for k, spec in MODEL_REGISTRY.items():
            if spec.provider == provider:
                return k
    if settings.llm_provider in MODEL_REGISTRY:
        return settings.llm_provider
    return registry_default_key()


@router.get('')
async def chat_page(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    conversation_ref: str | None = None,
):
    if conversation_ref is None:
        new_ref = f'CONV-{uuid.uuid4().hex[:8].upper()}'
        return RedirectResponse(f'/chat?conversation_ref={new_ref}', status_code=303)

    items = await repo.conversation_list(session, user.username)
    groups = group_conversations(items)
    active_ref = conversation_ref

    history: list = []
    if conversation_ref:
        try:
            history = await repo.get_conversation_history(session, conversation_ref)
            for turn in history:
                turn['badge_class'] = badge_class_for(turn.get('badge'))
        except Exception:
            history = []

    # Restore a stale-pending proposed action from PostgreSQL if Redis has
    # expired since the user last saw the Confirm card. We re-mint a fresh
    # HMAC token (same idempotency_key — a duplicate confirm still produces
    # exactly one row) and stage it back into Redis so /actions/confirm just
    # works as before.
    pending_action: dict | None = None
    try:
        pending_action = await get_pending_action(conversation_ref)
        if not pending_action:
            recovered = await repo.get_latest_pending_proposal(session, conversation_ref)
            if recovered:
                import time as _time
                recovered['confirmation_token'] = mint_confirmation_token(
                    recovered.get('trace_ref', ''),
                    recovered.get('action_type', ''),
                    recovered.get('issue_ref', ''),
                )
                recovered['expires_at'] = int(_time.time()) + 600
                await stage_pending_action(conversation_ref, recovered)
                pending_action = recovered
    except Exception:
        pending_action = None

    visible = visible_registry()
    # Pick a sensible default for the UI: prefer the configured one if visible,
    # otherwise the first visible model. Stub stays as the silent fallback in
    # the backend but never shows as a peer choice in the dropdown.
    if settings.llm_provider in visible:
        default_key = settings.llm_provider
    else:
        default_key = registry_default_key() if registry_default_key() in visible else next(iter(visible))

    return request.app.state.templates.TemplateResponse(
        request,
        'chat.html',
        {
            'user': user,
            'default_model_key': default_key,
            'conversation_ref': active_ref,
            'model_registry': visible,
            'conversation_groups': groups,
            'history': history,
            'pending_action': pending_action,
        },
    )


@router.post('')
async def chat(
    payload: ChatInput,
    user: CurrentUser = Depends(get_current_user),
    x_llm_provider: str | None = Header(default=None),
    x_llm_model: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    model_key = _resolve_model(payload.model_key or x_llm_model, payload.provider or x_llm_provider)
    return await run_agent(
        session=session,
        query=payload.query,
        username=user.username,
        role=user.primary_role,
        conversation_ref=payload.conversation_ref,
        provider_name=model_key,
        resolution_route=payload.resolution_route,
    )


@router.get('/stream')
async def chat_stream(
    query: str,
    conversation_ref: str = 'CONV-DEMO',
    model_key: str | None = None,
    provider: str | None = None,
    resolution_route: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    x_llm_provider: str | None = Header(default=None),
    x_llm_model: str | None = Header(default=None),
) -> EventSourceResponse:
    resolved_key = _resolve_model(model_key or x_llm_model, provider or x_llm_provider)
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def sink(event_name: str, payload: dict) -> None:
        await queue.put((event_name, payload))

    async def run_and_close() -> None:
        try:
            result = await run_agent(
                session=session, query=query, username=user.username,
                role=user.primary_role, conversation_ref=conversation_ref,
                provider_name=resolved_key, event_sink=sink,
                resolution_route=resolution_route,
            )
            await queue.put(('done', result.model_dump(mode='json')))
        except Exception as exc:
            await queue.put(('agent_error', {'error': str(exc)}))
        finally:
            await queue.put(None)

    async def event_gen() -> AsyncIterator[dict]:
        task = asyncio.create_task(run_and_close())
        yield {'event': 'planning', 'data': json.dumps({'status': 'Planning...'})}
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event_name, payload = item
                yield {'event': event_name, 'data': json.dumps(payload, default=str)}
        finally:
            if not task.done():
                task.cancel()

    return EventSourceResponse(event_gen())
