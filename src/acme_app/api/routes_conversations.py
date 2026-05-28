"""Conversations API.

The standalone /conversations page is gone — the sidebar in /chat is the
archive (ChatGPT-style). We keep:

  GET /conversations         → redirect to /chat
  GET /conversations/api     → JSON used by the sidebar
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.api._view_helpers import group_conversations
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.db.session import get_db_session


router = APIRouter(prefix='/conversations', tags=['conversations'])


@router.get('')
async def conversations_redirect() -> RedirectResponse:
    return RedirectResponse(url='/chat', status_code=302)


@router.get('/api')
async def list_conversations(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    items = await repo.conversation_list(session, user.username)
    return {
        'items': items,
        'groups': [
            {'name': name, 'rows': rows}
            for name, rows in group_conversations(items)
        ],
    }
