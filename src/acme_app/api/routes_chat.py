"""Chat routes: POST /chat (non-streaming) and GET /chat/stream (SSE).

Both go through the same orchestrator. The SSE variant uses an event sink to
stream progress; the POST variant ignores the sink and returns the final
ChatResponse JSON.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from acme_app.application.orchestrator import run_agent
from acme_app.application.schemas import ChatResponse
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.config import settings
from acme_app.infrastructure.db.session import get_db_session

router = APIRouter(prefix='/chat', tags=['chat'])


class ChatInput(BaseModel):
    query: str
    conversation_ref: str = 'CONV-DEMO'
    provider: str | None = None


@router.get('', response_class=HTMLResponse)
async def chat_page(request: Request, user: CurrentUser = Depends(get_current_user)) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request,
        'chat.html',
        {
            'user': user,
            'provider': settings.llm_provider,
            'conversation_ref': 'CONV-DEMO',
            'providers': ['stub', 'anthropic', 'openai', 'ollama'],
        },
    )


@router.post('')
async def chat(
    payload: ChatInput,
    user: CurrentUser = Depends(get_current_user),
    x_llm_provider: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    provider = payload.provider or x_llm_provider or settings.llm_provider
    return await run_agent(
        session=session,
        query=payload.query,
        username=user.username,
        role=user.primary_role,
        conversation_ref=payload.conversation_ref,
        provider_name=provider,
    )


@router.get('/stream')
async def chat_stream(
    query: str,
    conversation_ref: str = 'CONV-DEMO',
    provider: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    x_llm_provider: str | None = Header(default=None),
) -> EventSourceResponse:
    provider_name = provider or x_llm_provider or settings.llm_provider
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def sink(event_name: str, payload: dict) -> None:
        await queue.put((event_name, payload))

    async def run_and_close() -> None:
        try:
            result = await run_agent(
                session=session, query=query, username=user.username,
                role=user.primary_role, conversation_ref=conversation_ref,
                provider_name=provider_name, event_sink=sink,
            )
            await queue.put(('done', result.model_dump(mode='json')))
        except Exception as exc:
            await queue.put(('error', {'error': str(exc)}))
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
