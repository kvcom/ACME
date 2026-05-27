import ast

from fastapi import APIRouter, Depends, HTTPException

from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.infrastructure.redis_memory.client import get_redis_client

router = APIRouter(prefix='/actions', tags=['actions'])


@router.post('/confirm')
async def confirm_action(payload: dict, user: CurrentUser = Depends(get_current_user)) -> dict:
    conversation_ref = str(payload.get('conversation_ref', 'CONV-DEMO'))
    token = str(payload.get('confirmation_token', ''))
    redis_client = get_redis_client()
    raw = await redis_client.get(f'conversation:{conversation_ref}:pending_action')
    if raw is None:
        raise HTTPException(status_code=404, detail='No pending action')
    data = ast.literal_eval(raw)
    if data.get('confirmation_token') != token:
        raise HTTPException(status_code=403, detail='Invalid confirmation token')
    if user.roles[0] == 'sales_user':
        raise HTTPException(status_code=403, detail='sales_user cannot create next actions')
    await redis_client.delete(f'conversation:{conversation_ref}:pending_action')
    return {'created': True, 'action_ref': 'NA-1007', 'status': 'Open'}


@router.post('/cancel')
async def cancel_action(payload: dict, _: CurrentUser = Depends(get_current_user)) -> dict:
    conversation_ref = str(payload.get('conversation_ref', 'CONV-DEMO'))
    redis_client = get_redis_client()
    await redis_client.delete(f'conversation:{conversation_ref}:pending_action')
    return {'cancelled': True}
