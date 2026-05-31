"""Trace viewer routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.api._view_helpers import badge_class_for, enrich_trace_row, relative_when
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.config import settings
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.db.session import get_db_session

router = APIRouter(prefix='/traces', tags=['traces'])


def _can_read_trace(user: CurrentUser, trace: dict) -> bool:
    return 'admin' in user.roles or trace.get('username') == user.username


@router.get('', response_class=HTMLResponse)
async def traces_page(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    username = None if 'admin' in user.roles else user.username
    traces = [enrich_trace_row(r) for r in await repo.list_traces(session, limit=100, username=username)]
    return request.app.state.templates.TemplateResponse(
        request, 'traces.html', {'user': user, 'traces': traces},
    )


@router.get('/api')
async def traces_api(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    username = None if 'admin' in user.roles else user.username
    traces = await repo.list_traces(session, limit=100, username=username)
    return {'items': traces}


@router.get('/{trace_ref}', response_class=HTMLResponse)
async def trace_detail(
    trace_ref: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    trace = await repo.get_trace(session, trace_ref)
    if trace is None:
        raise HTTPException(status_code=404, detail='Trace not found')
    if not _can_read_trace(user, trace):
        raise HTTPException(status_code=404, detail='Trace not found')
    trace['badge_class'] = badge_class_for(trace.get('final_status'))
    trace['when'] = relative_when(trace.get('created_at'))
    is_admin = 'admin' in user.roles
    return request.app.state.templates.TemplateResponse(
        request, 'trace_detail.html',
        {'user': user, 'trace': trace, 'admin_reveal': is_admin},
    )


@router.get('/{trace_ref}/otel')
async def trace_otel(
    trace_ref: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """OTel span summary for the shared trace popover (same shape as the
    DB Explorer's /db-explorer/otel endpoint). Authz: admin or trace owner."""
    trace = await repo.get_trace(session, trace_ref)
    if trace is None or not _can_read_trace(user, trace):
        raise HTTPException(status_code=404, detail='Trace not found')
    return {
        'otel_trace_id': trace.get('otel_trace_id') or '',
        'jaeger_url': (
            f"{settings.otel_jaeger_ui_url.rstrip('/')}/trace/{trace.get('otel_trace_id')}"
            if trace.get('otel_trace_id') else ''
        ),
        'trace_ref': trace['trace_ref'],
        'detected_intent': trace.get('detected_intent'),
        'final_status': trace.get('final_status'),
        'llm_provider': trace.get('provider'),
        'llm_model': trace.get('model'),
        'total_latency_ms': trace.get('total_latency_ms'),
        'llm_latency_ms': trace.get('llm_latency_ms'),
        'tool_latency_ms': trace.get('tool_latency_ms'),
        'total_tokens': trace.get('total_tokens'),
        'estimated_cost_usd': trace.get('cost_usd'),
        'created_at': trace.get('created_at'),
        'spans': [
            {
                'event_type': e.get('event_type'),
                'event_name': e.get('event_name'),
                'status': e.get('status'),
                'latency_ms': e.get('latency_ms'),
                'at': e.get('created_at'),
            }
            for e in (trace.get('events') or [])
        ],
    }


@router.get('/{trace_ref}/json')
async def trace_json(
    trace_ref: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    trace = await repo.get_trace(session, trace_ref)
    if trace is None:
        raise HTTPException(status_code=404, detail='Trace not found')
    if not _can_read_trace(user, trace):
        raise HTTPException(status_code=404, detail='Trace not found')
    return trace
