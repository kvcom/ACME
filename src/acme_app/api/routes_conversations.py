from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.api._view_helpers import group_conversations
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.db.session import get_db_session


router = APIRouter(prefix='/conversations', tags=['conversations'])


@router.get('', response_class=HTMLResponse)
async def conversations_page(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    items = await repo.conversation_list(session, user.username)
    return request.app.state.templates.TemplateResponse(
        request, 'conversations.html',
        {'user': user, 'groups': group_conversations(items)},
    )


@router.get('/api')
async def list_conversations(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    items = await repo.conversation_list(session, user.username)
    return {'items': items}
