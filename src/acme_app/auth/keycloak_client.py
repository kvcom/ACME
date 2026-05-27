"""Thin Keycloak adapter (direct-access-grant flow).

Direct grant is chosen for MVP per DECISION_LOG D-003; production should use
Authorization Code with PKCE.
"""
from __future__ import annotations

import httpx

from acme_app.config import settings


class KeycloakError(Exception):
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
                raise KeycloakError(f'login failed ({response.status_code}): {response.text[:200]}')
            return response.json()
    except httpx.HTTPError as exc:
        raise KeycloakError(f'Keycloak transport error: {exc}') from exc


async def health() -> bool:
    url = f'{settings.keycloak_url}/realms/{settings.keycloak_realm}'
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            return response.status_code == 200
    except httpx.HTTPError:
        return False
