"""Conversations API.

The standalone /conversations page is gone — the sidebar in /chat is the
archive (ChatGPT-style). We keep:

  GET /conversations         → redirect to /chat
  GET /conversations/api     → JSON used by the sidebar
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.api._view_helpers import group_conversations
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.db.session import get_db_session


router = APIRouter(prefix='/conversations', tags=['conversations'])


@router.delete('/{conversation_ref}')
async def delete_conversation(
    conversation_ref: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Soft-delete a conversation. The underlying audit trail (agent_traces,
    trace_events, tool_call_logs, rbac_decisions) is preserved per the
    Decision Ledger principle. Only the user-facing visibility is removed.
    See DECISION_LOG D-015."""
    ok = await repo.soft_delete_conversation(session, conversation_ref, user.username)
    if not ok:
        raise HTTPException(status_code=404, detail='conversation not found or already deleted')
    return {'deleted': True, 'conversation_ref': conversation_ref, 'soft': True}


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
