"""JWT decoding and verification.

Bearer tokens issued by Keycloak are verified against the realm's JWKS
(RS256) before any claim is trusted — this closes the forge-an-admin-JWT
hole that `get_unverified_claims` left open. Verification is on by default and
can only be disabled via `jwt_verify_signature=false` for an offline /
no-Keycloak demo (see DECISION_LOG D-004 / D-022).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

from fastapi import HTTPException
from jose import jwt
from jose.exceptions import JWTError

from acme_app.config import settings

logger = logging.getLogger(__name__)

# Module-level JWKS cache. Keycloak rotates signing keys rarely; we cache the
# key set and refresh once on an unknown-kid (handled by re-fetch + retry).
_JWKS_CACHE: dict[str, Any] | None = None
_JWKS_FETCHED_AT: float = 0.0
_JWKS_TTL_S = 3600


def _jwks_url() -> str:
    base = settings.keycloak_url.rstrip('/')
    return f'{base}/realms/{settings.keycloak_realm}/protocol/openid-connect/certs'


def _fetch_jwks(force: bool = False) -> dict[str, Any]:
    global _JWKS_CACHE, _JWKS_FETCHED_AT
    fresh = _JWKS_CACHE is not None and (time.time() - _JWKS_FETCHED_AT) < _JWKS_TTL_S
    if fresh and not force:
        return _JWKS_CACHE  # type: ignore[return-value]
    with urllib.request.urlopen(_jwks_url(), timeout=5) as resp:  # noqa: S310 (trusted internal URL)
        data = json.loads(resp.read().decode())
    _JWKS_CACHE = data
    _JWKS_FETCHED_AT = time.time()
    return data


def decode_token(token: str) -> dict[str, Any]:
    """Decode a bearer token. Verifies the RS256 signature against the realm
    JWKS unless `jwt_verify_signature` is explicitly disabled."""
    if not settings.jwt_verify_signature:
        logger.warning('JWT signature verification DISABLED (jwt_verify_signature=false)')
        return jwt.get_unverified_claims(token)

    # Keycloak may not set an audience the client expects; we verify signature,
    # expiry and issuer but not audience.
    issuer = f'{settings.keycloak_url.rstrip("/")}/realms/{settings.keycloak_realm}'
    options = {'verify_aud': False}
    for force in (False, True):  # one retry with a forced JWKS refresh on key miss
        try:
            return jwt.decode(
                token,
                _fetch_jwks(force=force),
                algorithms=['RS256'],
                issuer=issuer,
                options=options,
            )
        except JWTError as exc:
            msg = str(exc).lower()
            if not force and ('key' in msg or 'kid' in msg):
                continue  # signing key may have rotated — refresh and retry once
            raise HTTPException(status_code=401, detail=f'Invalid token: {exc}') from exc
        except Exception as exc:  # JWKS fetch failure, etc.
            raise HTTPException(status_code=503, detail=f'Cannot verify token: {exc}') from exc
    # Unreachable, but keeps the type checker happy.
    raise HTTPException(status_code=401, detail='Invalid token')


def extract_roles(claims: dict[str, Any]) -> list[str]:
    realm_access = claims.get('realm_access') or {}
    roles = realm_access.get('roles') or []
    return [r for r in roles if r in {'sales_user', 'support_user', 'admin'}]
