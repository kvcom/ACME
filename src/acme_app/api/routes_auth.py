"""Auth routes: token login + demo cookie login (skips Keycloak when down).

The cookie login is gated to the three known demo usernames; it issues a
session cookie that encodes the chosen role exactly as if it had come from
Keycloak. Documented in DECISION_LOG D-005.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from acme_app.auth.current_user import CurrentUser, _encode_session, get_current_user, user_from_token
from acme_app.auth.keycloak_client import KeycloakError, login as keycloak_login
from acme_app.config import settings


router = APIRouter(tags=['auth'])


DEMO_USERS = {
    'sarah.sales': {'roles': ['sales_user'], 'password': 'password', 'display': 'Sarah Sales'},
    'sam.support': {'roles': ['support_user'], 'password': 'password', 'display': 'Sam Support'},
    'admin.acme': {'roles': ['admin'], 'password': 'password', 'display': 'Admin Acme'},
}


class LoginInput(BaseModel):
    username: str
    password: str


@router.get('/login', response_class=HTMLResponse)
async def login_page(request: Request, next: str = Query(default='/chat')) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, 'login.html', {'error': None, 'next': next})


@router.post('/login')
async def login_form(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Query(default='/chat'),
) -> HTMLResponse:
    user, error = await _resolve_login(username, password)
    if user is None:
        return request.app.state.templates.TemplateResponse(
            request, 'login.html', {'error': error or 'Invalid credentials', 'next': next}, status_code=401,
        )
    destination = next if next.startswith('/') else '/chat'
    response = RedirectResponse(url=destination, status_code=303)
    response.set_cookie(
        'acme_session',
        _encode_session(user),
        httponly=True,
        samesite='lax',
        max_age=settings.demo_session_max_age_seconds,
    )
    return response


@router.post('/auth/login')
async def auth_login(payload: LoginInput) -> dict:
    user, error = await _resolve_login(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail=error or 'Invalid credentials')
    return {
        'subject': user.subject, 'username': user.username, 'roles': user.roles,
        'access_token': user.access_token or 'demo-cookie-session',
    }


@router.get('/auth/me')
async def auth_me(user: CurrentUser = Depends(get_current_user)) -> dict:
    return {'subject': user.subject, 'username': user.username, 'roles': user.roles}


@router.post('/logout')
async def logout() -> RedirectResponse:
    response = RedirectResponse(url='/login', status_code=303)
    response.delete_cookie('acme_session')
    return response


async def _resolve_login(username: str, password: str) -> tuple[CurrentUser | None, str | None]:
    """Try Keycloak first; if it's unreachable or rejects, fall back to the demo table."""
    try:
        token_payload = await keycloak_login(username, password)
        access_token = token_payload.get('access_token', '')
        if access_token:
            try:
                return user_from_token(access_token), None
            except HTTPException as exc:
                return None, exc.detail
    except KeycloakError:
        pass
    demo = DEMO_USERS.get(username)
    if demo and demo['password'] == password:
        return CurrentUser(
            subject=f'demo-{username}', username=username,
            roles=list(demo['roles']), access_token='',
        ), None
    return None, 'Invalid credentials'
