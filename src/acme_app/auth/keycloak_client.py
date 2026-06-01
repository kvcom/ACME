"""Thin Keycloak adapter (direct-access-grant flow).

Direct grant is chosen for MVP per DECISION_LOG D-003; production should use
Authorization Code with PKCE.
"""
from __future__ import annotations

import httpx

from acme_app.config import settings


class KeycloakError(Exception):
    pass


class KeycloakLoginRejected(KeycloakError):
    pass


class KeycloakAccountNotReady(KeycloakError):
    pass


class KeycloakUnavailable(KeycloakError):
    pass


async def login(username: str, password: str) -> dict:
    url = f'{settings.keycloak_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/token'
    payload = {
        'client_id': settings.keycloak_client_id,
        'grant_type': 'password',
        'username': username,
        'password': password,
    }
    if settings.keycloak_client_secret:
        payload['client_secret'] = settings.keycloak_client_secret
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, data=payload)
            if response.status_code >= 400:
                body = response.text[:200]
                if 'Account is not fully set up' in body:
                    raise KeycloakAccountNotReady(f'login failed ({response.status_code}): {body}')
                raise KeycloakLoginRejected(f'login failed ({response.status_code}): {body}')
            return response.json()
    except httpx.HTTPError as exc:
        raise KeycloakUnavailable(f'Keycloak transport error: {exc}') from exc


async def session_active(subject: str, session_id: str) -> bool:
    """Return whether Keycloak still has this user's SSO session.

    The app stores Keycloak's `sid` claim in its signed cookie. Force-signing
    out a user in Keycloak removes that session id immediately; this check lets
    ACME reject its local cookie instead of waiting for cookie expiry.
    """
    if not settings.keycloak_session_enforcement_enabled:
        return True
    if not subject or not session_id:
        return False
    admin_token = await _admin_access_token()
    url = (
        f'{settings.keycloak_url}/admin/realms/{settings.keycloak_realm}'
        f'/users/{subject}/sessions'
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, headers={'Authorization': f'Bearer {admin_token}'})
            if response.status_code == 404:
                return False
            response.raise_for_status()
            sessions = response.json()
            return any(str(item.get('id')) == session_id for item in sessions)
    except httpx.HTTPError as exc:
        raise KeycloakUnavailable(f'Keycloak session check failed: {exc}') from exc


async def _admin_access_token() -> str:
    url = f'{settings.keycloak_url}/realms/master/protocol/openid-connect/token'
    payload = {
        'client_id': 'admin-cli',
        'grant_type': 'password',
        'username': settings.keycloak_admin_username,
        'password': settings.keycloak_admin_password,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, data=payload)
            response.raise_for_status()
            token = response.json().get('access_token')
            if not token:
                raise KeycloakUnavailable('Keycloak admin token response had no access_token')
            return str(token)
    except httpx.HTTPError as exc:
        raise KeycloakUnavailable(f'Keycloak admin token error: {exc}') from exc


async def health() -> bool:
    url = f'{settings.keycloak_url}/realms/{settings.keycloak_realm}'
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            return response.status_code == 200
    except httpx.HTTPError:
        return False
