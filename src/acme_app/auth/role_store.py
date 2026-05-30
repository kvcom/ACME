"""Postgres-backed role lookup.

Authentication is Keycloak's job (it verifies the password and issues the
token); authorization is Postgres's job (it stores which roles each user
has). This split is logged as D-016 in DECISION_LOG.

The lookup runs at login time and the resolved roles are then carried in
the signed session cookie for the rest of the session — we don't re-hit
the DB on every request. Trade-off: a role revocation only takes effect
on next login. Acceptable for MVP and consistent with typical JWT systems.
"""
from __future__ import annotations

from sqlalchemy import text

from acme_app.infrastructure.db.session import AsyncSessionLocal


SUPPORTED_ROLES = {'sales_user', 'support_user', 'admin'}


async def link_keycloak_subject(username: str, subject: str) -> None:
    """Stamp the Keycloak `sub` onto `users.keycloak_subject` on first login.

    No-op when the column is already populated or the user does not exist.
    Best-effort: a DB hiccup here must not block the login that just
    succeeded, so callers swallow exceptions.
    """
    if not subject or subject.startswith('demo-'):
        return
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                UPDATE users
                SET keycloak_subject = :sub
                WHERE username = :u
                  AND keycloak_subject IS NULL
            """),
            {'u': username, 'sub': subject},
        )
        await session.commit()


async def get_roles_for_username(username: str) -> list[str] | None:
    """Return active roles for a username, or None if the user does not exist
    (or is inactive/soft-deleted). Roles are filtered to SUPPORTED_ROLES.

    On DB error this raises — callers decide whether to fall back.
    """
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            text("""
                SELECT u.id::text, u.is_active, u.deleted_at,
                       COALESCE(array_agg(ur.role_name) FILTER (WHERE ur.role_name IS NOT NULL), '{}')
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                WHERE u.username = :u
                GROUP BY u.id, u.is_active, u.deleted_at
            """),
            {'u': username},
        )).first()
    if row is None:
        return None
    _user_id, is_active, deleted_at, role_names = row
    if not is_active or deleted_at is not None:
        return None
    return [r for r in role_names if r in SUPPORTED_ROLES]
