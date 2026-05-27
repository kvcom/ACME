"""Confirm / cancel routes for the propose-confirm flow.

Confirm: validates the HMAC confirmation_token, re-checks RBAC, then forwards
to MCP create_next_action. The re-check is deliberate — never trust the token
alone for authorisation.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.application.propose_confirm import clear_pending_action, get_pending_action
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.db.session import get_db_session
from acme_app.infrastructure.mcp_client.client import MCPClient, MCPClientError
from acme_app.policy.action_guard import can_propose, verify_confirmation_token


router = APIRouter(prefix='/actions', tags=['actions'])


class ConfirmInput(BaseModel):
    conversation_ref: str = 'CONV-DEMO'
    confirmation_token: str


class CancelInput(BaseModel):
    conversation_ref: str = 'CONV-DEMO'


@router.post('/confirm')
async def confirm_action(
    payload: ConfirmInput,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    pending = await get_pending_action(payload.conversation_ref)
    if pending is None:
        raise HTTPException(status_code=404, detail='No pending action')
    if pending['confirmation_token'] != payload.confirmation_token:
        raise HTTPException(status_code=403, detail='confirmation_token mismatch')

    ok_tok, why_tok, _ = verify_confirmation_token(payload.confirmation_token)
    if not ok_tok:
        raise HTTPException(status_code=403, detail=f'token invalid: {why_tok}')

    role = user.primary_role
    allowed, reason = can_propose(role, pending['action_type'])
    await repo.insert_rbac_decision(
        session, trace_id=None, username=user.username, role=role,
        operation='create_action', resource=pending['action_type'],
        allowed=allowed, reason=reason,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)

    mcp = MCPClient()
    try:
        result = await mcp.call_tool('create_next_action', {
            'actor': {'username': user.username, 'role': role},
            'issue_ref': pending['issue_ref'],
            'action_type': pending['action_type'],
            'title': pending.get('title', ''),
            'description': pending.get('description', ''),
            'priority': pending.get('priority', 'Medium'),
            'due_at': pending.get('due_at'),
            'evidence': pending.get('evidence', []),
            'idempotency_key': pending['idempotency_key'],
            'confirmation_token': pending['confirmation_token'],
        })
    except MCPClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if result.get('created') or result.get('duplicate'):
        await clear_pending_action(payload.conversation_ref)
    return result


@router.post('/cancel')
async def cancel_action(
    payload: CancelInput,
    _user: CurrentUser = Depends(get_current_user),
) -> dict:
    await clear_pending_action(payload.conversation_ref)
    return {'cancelled': True}


@router.get('/pending')
async def pending_action(
    conversation_ref: str = 'CONV-DEMO',
    _user: CurrentUser = Depends(get_current_user),
) -> dict:
    pending = await get_pending_action(conversation_ref)
    return {'pending': pending}
