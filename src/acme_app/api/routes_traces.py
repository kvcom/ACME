"""Trace viewer routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.api._view_helpers import badge_class_for, enrich_trace_row, relative_when
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.db.session import get_db_session


router = APIRouter(prefix='/traces', tags=['traces'])


@router.get('', response_class=HTMLResponse)
async def traces_page(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    traces = [enrich_trace_row(r) for r in await repo.list_traces(session, limit=100)]
    return request.app.state.templates.TemplateResponse(
        request, 'traces.html', {'user': user, 'traces': traces},
    )


@router.get('/api')
async def traces_api(
    _user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    traces = await repo.list_traces(session, limit=100)
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
    trace['badge_class'] = badge_class_for(trace.get('final_status'))
    trace['when'] = relative_when(trace.get('created_at'))
    is_admin = 'admin' in user.roles
    return request.app.state.templates.TemplateResponse(
        request, 'trace_detail.html',
        {'user': user, 'trace': trace, 'admin_reveal': is_admin},
    )


@router.get('/{trace_ref}/json')
async def trace_json(
    trace_ref: str,
    _user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    trace = await repo.get_trace(session, trace_ref)
    if trace is None:
        raise HTTPException(status_code=404, detail='Trace not found')
    return trace
