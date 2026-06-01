"""FastAPI dependency that resolves the current user from a bearer token or a
demo session cookie. The cookie path keeps the UI simple while staying within
the Keycloak-issued role envelope.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import Cookie, Header, HTTPException

from acme_app.auth.keycloak_client import KeycloakUnavailable, session_active
from acme_app.auth.jwt_validator import decode_token, extract_roles
from acme_app.config import settings

VALID_ROLES = {'sales_user', 'support_user', 'admin'}
SESSION_COOKIE = 'acme_session'


def _sign(payload_b64: str) -> str:
    """HMAC-SHA256 over the base64 payload, returned as urlsafe-b64 (no pad)."""
    sig = hmac.new(
        settings.session_signing_secret.encode(), payload_b64.encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip('=')


@dataclass(frozen=True)
class CurrentUser:
    subject: str
    username: str
    roles: list[str]
    access_token: str = ''
    auth_source: str = 'keycloak'
    keycloak_session_id: str = ''

    @property
    def primary_role(self) -> str:
        order = ['admin', 'support_user', 'sales_user']
        for role in order:
            if role in self.roles:
                return role
        return self.roles[0] if self.roles else 'guest'


def _encode_session(user: CurrentUser) -> str:
    """Produce a tamper-evident session cookie: `<payload_b64>.<hmac_sig>`.

    The payload is signed with HMAC-SHA256. A client cannot change the roles,
    username or any field without invalidating the signature, so the cookie
    cannot be forged to escalate privilege (the previous base64-only form
    could be).
    """
    raw = json.dumps({
        'sub': user.subject,
        'u': user.username,
        'r': user.roles,
        't': user.access_token,
        's': user.auth_source,
        'sid': user.keycloak_session_id,
        'exp': int(time.time()) + settings.demo_session_max_age_seconds,
    }).encode()
    payload_b64 = base64.urlsafe_b64encode(raw).decode()
    return f'{payload_b64}.{_sign(payload_b64)}'


def _decode_session(token: str) -> CurrentUser | None:
    try:
        # Verify signature before trusting any field. Constant-time compare.
        payload_b64, _, sig = token.partition('.')
        if not sig or not hmac.compare_digest(sig, _sign(payload_b64)):
            return None
        raw = base64.urlsafe_b64decode(payload_b64.encode())
        data = json.loads(raw)
        expires_at = int(data.get('exp') or 0)
        if not expires_at or expires_at < int(time.time()):
            return None
        auth_source = str(data.get('s') or ('keycloak' if data.get('t') else 'demo_fallback'))
        return CurrentUser(
            subject=data['sub'],
            username=data['u'],
            roles=list(data['r']),
            access_token=data.get('t', ''),
            auth_source=auth_source,
            keycloak_session_id=str(data.get('sid') or ''),
        )
    except Exception:
        return None


def session_cookie_for(user: CurrentUser) -> tuple[str, str]:
    return SESSION_COOKIE, _encode_session(user)


def user_from_token(token: str) -> CurrentUser:
    claims = decode_token(token)
    roles = extract_roles(claims)
    if not roles:
        raise HTTPException(status_code=403, detail='No supported role in token')
    return CurrentUser(
        subject=str(claims.get('sub', 'unknown')),
        username=str(claims.get('preferred_username', 'unknown')),
        roles=roles,
        access_token=token,
        auth_source='keycloak',
        keycloak_session_id=str(claims.get('sid') or claims.get('session_state') or ''),
    )


async def _ensure_keycloak_session_active(user: CurrentUser) -> None:
    if user.auth_source != 'keycloak':
        return
    try:
        if await session_active(user.subject, user.keycloak_session_id):
            return
    except KeycloakUnavailable as exc:
        raise HTTPException(status_code=503, detail='Cannot verify Keycloak session') from exc
    raise HTTPException(status_code=401, detail='Keycloak session is no longer active')


async def get_current_user(
    authorization: str = Header(default=''),
    acme_session: str | None = Cookie(default=None),
) -> CurrentUser:
    if authorization.startswith('Bearer '):
        user = user_from_token(authorization.removeprefix('Bearer ').strip())
        await _ensure_keycloak_session_active(user)
        return user
    if acme_session:
        user = _decode_session(acme_session)
        if user is not None:
            await _ensure_keycloak_session_active(user)
            return user
    raise HTTPException(status_code=401, detail='Not authenticated')


async def get_optional_user(
    authorization: str = Header(default=''),
    acme_session: str | None = Cookie(default=None),
) -> CurrentUser | None:
    try:
        return await get_current_user(authorization=authorization, acme_session=acme_session)
    except HTTPException:
        return None
