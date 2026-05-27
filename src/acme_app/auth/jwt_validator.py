"""JWT decoding.

For MVP we decode without signature verification. Production should fetch the
realm's JWKS and verify per Keycloak guidance — captured as D-004 in DECISION_LOG.
"""
from __future__ import annotations

from typing import Any

from jose import jwt


def decode_token(token: str) -> dict[str, Any]:
    return jwt.get_unverified_claims(token)


def extract_roles(claims: dict[str, Any]) -> list[str]:
    realm_access = claims.get('realm_access') or {}
    roles = realm_access.get('roles') or []
    return [r for r in roles if r in {'sales_user', 'support_user', 'admin'}]
