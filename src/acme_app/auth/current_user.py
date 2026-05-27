"""FastAPI dependency that resolves the current user from a bearer token or a
demo session cookie. The cookie path keeps the UI simple while staying within
the Keycloak-issued role envelope.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from fastapi import Cookie, Header, HTTPException

from acme_app.auth.jwt_validator import decode_token, extract_roles

VALID_ROLES = {'sales_user', 'support_user', 'admin'}
SESSION_COOKIE = 'acme_session'


@dataclass(frozen=True)
class CurrentUser:
    subject: str
    username: str
    roles: list[str]
    access_token: str = ''

    @property
    def primary_role(self) -> str:
        order = ['admin', 'support_user', 'sales_user']
        for role in order:
            if role in self.roles:
                return role
        return self.roles[0] if self.roles else 'guest'


def _encode_session(user: CurrentUser) -> str:
    raw = json.dumps({'sub': user.subject, 'u': user.username, 'r': user.roles, 't': user.access_token}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_session(token: str) -> CurrentUser | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        data = json.loads(raw)
        return CurrentUser(subject=data['sub'], username=data['u'], roles=list(data['r']), access_token=data.get('t', ''))
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
    )


async def get_current_user(
    authorization: str = Header(default=''),
    acme_session: str | None = Cookie(default=None),
) -> CurrentUser:
    if authorization.startswith('Bearer '):
        return user_from_token(authorization.removeprefix('Bearer ').strip())
    if acme_session:
        user = _decode_session(acme_session)
        if user is not None:
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
