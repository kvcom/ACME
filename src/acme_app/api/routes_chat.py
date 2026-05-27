import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from acme_app.application.orchestrator import run_agent
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.config import settings

router = APIRouter(prefix='/chat', tags=['chat'])


@router.get('', response_class=HTMLResponse)
async def chat_page(request: Request, user: CurrentUser = Depends(get_current_user)) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse('chat.html', {'request': request, 'user': user, 'provider': settings.llm_provider, 'conversation_ref': 'CONV-DEMO'})


@router.post('')
async def chat(payload: dict, user: CurrentUser = Depends(get_current_user)) -> dict:
    return await run_agent(str(payload.get('query', '')), str(payload.get('provider', settings.llm_provider)), user.roles[0], 'CONV-DEMO')


@router.get('/stream')
async def chat_stream(query: str, provider: str = settings.llm_provider, user: CurrentUser = Depends(get_current_user)) -> EventSourceResponse:
    async def event_gen():
        yield {'event': 'planning', 'data': json.dumps({'status': 'Planning...'})}
        await asyncio.sleep(0.1)
        result = await run_agent(query, provider, user.roles[0], 'CONV-DEMO')
        for event in result.get('events', []):
            yield {'event': 'tool_complete', 'data': json.dumps(event)}
        if result.get('proposed_action'):
            yield {'event': 'proposed_action', 'data': json.dumps(result['proposed_action'])}
        yield {'event': 'final_response', 'data': json.dumps(result)}
        yield {'event': 'trace', 'data': json.dumps({'trace_ref': result['trace_ref'], 'cost_usd': result['cost_usd'], 'total_tokens': result['total_tokens']})}

    return EventSourceResponse(event_gen())
