from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.auth.keycloak_client import login

router = APIRouter(prefix='/auth', tags=['auth'])


class LoginInput(BaseModel):
    username: str
    password: str


@router.post('/login')
async def auth_login(payload: LoginInput) -> dict:
    try:
        return await login(payload.username, payload.password)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get('/me')
async def auth_me(user: CurrentUser = Depends(get_current_user)) -> dict:
    return {'subject': user.subject, 'username': user.username, 'roles': user.roles}
