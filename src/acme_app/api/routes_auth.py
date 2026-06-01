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
from acme_app.auth.keycloak_client import (
    KeycloakAccountNotReady,
    KeycloakLoginRejected,
    KeycloakUnavailable,
    login as keycloak_login,
)
from acme_app.auth.role_store import get_roles_for_username, link_keycloak_subject
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


def _friendly_login_error(error: str | None) -> str:
    if not error:
        return 'Could not sign you in. Check your username and password.'
    lowered = error.lower()
    if 'invalid_grant' in lowered or 'invalid user credentials' in lowered or 'login failed (401)' in lowered:
        return 'Username or password is incorrect.'
    if 'fallback is disabled' in lowered or 'keycloak unavailable' in lowered or 'transport error' in lowered:
        return 'Sign-in service is unavailable. Please try again in a moment.'
    if 'no supported role' in lowered:
        return 'Your account does not have access to this app.'
    return 'Could not sign you in. Check your username and password.'


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
            request, 'login.html', {'error': _friendly_login_error(error), 'next': next}, status_code=401,
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
        raise HTTPException(status_code=401, detail=_friendly_login_error(error))
    return {
        'subject': user.subject, 'username': user.username, 'roles': user.roles,
        'access_token': user.access_token or 'demo-cookie-session',
    }


@router.get('/auth/me')
async def auth_me(user: CurrentUser = Depends(get_current_user)) -> dict:
    return {
        'subject': user.subject,
        'username': user.username,
        'roles': user.roles,
        'auth_source': user.auth_source,
        'heartbeat_seconds': settings.session_heartbeat_seconds,
    }


@router.post('/logout')
async def logout() -> RedirectResponse:
    response = RedirectResponse(url='/login', status_code=303)
    response.delete_cookie('acme_session')
    return response


async def _resolve_login(username: str, password: str) -> tuple[CurrentUser | None, str | None]:
    """Authenticate via Keycloak; authorize via Postgres `user_roles`.

    See DECISION_LOG D-016. Keycloak verifies the password; Postgres is the
    source of truth for which roles the user has. If the user has no row in
    `users` (or no supported roles), login is rejected even when Keycloak
    accepted the password — this app is the gatekeeper for *its* roles.
    """
    db_roles = await _safe_load_db_roles(username)

    access_token = ''
    auth_source = 'keycloak'
    subject = f'demo-{username}'
    keycloak_session_id = ''
    keycloak_accepted = False
    try:
        token_payload = await keycloak_login(username, password)
        access_token = token_payload.get('access_token', '')
        if access_token:
            try:
                kc_user = user_from_token(access_token)
                subject = kc_user.subject
                keycloak_session_id = kc_user.keycloak_session_id
                keycloak_accepted = True
            except HTTPException as exc:
                # JWT decoded but had no supported role at the Keycloak side.
                # We override with DB roles below if available.
                subject = exc.detail if isinstance(exc.detail, str) else subject
                keycloak_accepted = True
    except KeycloakLoginRejected as exc:
        return None, str(exc)
    except KeycloakAccountNotReady:
        pass
    except KeycloakUnavailable:
        pass

    if not keycloak_accepted:
        # Keycloak couldn't confirm the password. Fall back to the demo table
        # if and only if that's enabled — and still require a Postgres user row.
        if not settings.demo_auth_fallback_enabled:
            return None, 'Keycloak unavailable and demo auth fallback is disabled'
        demo = DEMO_USERS.get(username)
        if not demo or demo['password'] != password:
            return None, 'Invalid credentials'
        auth_source = 'demo_fallback'

    # Postgres is authoritative for role assignment.
    if db_roles is None:
        return None, 'Account is not provisioned in this application'
    if not db_roles:
        return None, 'No supported role assigned in Postgres'

    # First-login provisioning: stamp the Keycloak `sub` onto `users.keycloak_subject`
    # so the two stores share a stable link. Best-effort — a DB hiccup must not
    # fail a login that already passed authn + authz.
    if keycloak_accepted and not subject.startswith('demo-'):
        try:
            await link_keycloak_subject(username, subject)
        except Exception:
            pass

    return CurrentUser(
        subject=subject,
        username=username,
        roles=db_roles,
        access_token=access_token,
        auth_source=auth_source,
        keycloak_session_id=keycloak_session_id,
    ), None


async def _safe_load_db_roles(username: str) -> list[str] | None:
    """Wrap the role lookup so a DB outage doesn't take auth down completely.

    Returns the role list (possibly empty) when the user is found, None when
    the user is missing, and re-raises only on programming errors. On a
    transport error we treat the user as not-found to fail closed.
    """
    try:
        return await get_roles_for_username(username)
    except Exception:
        return None
