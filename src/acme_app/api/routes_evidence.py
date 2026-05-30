"""Evidence inspection routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.db.session import get_db_session


router = APIRouter(prefix='/evidence', tags=['evidence'])


def split_evidence_ref(evidence: str) -> tuple[str, str]:
    if ':' not in evidence:
        raise ValueError('Evidence must be kind:identifier')
    kind, identifier = evidence.split(':', 1)
    kind = kind.strip().lower()
    identifier = identifier.strip()
    if not kind or not identifier:
        raise ValueError('Evidence must include kind and identifier')
    return kind, identifier


@router.get('/record')
async def evidence_record(
    evidence: str = Query(..., min_length=3),
    _user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        kind, identifier = split_evidence_ref(evidence)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved = await repo.get_evidence_record(session, kind, identifier)
    if resolved is None:
        raise HTTPException(status_code=404, detail='Evidence record not found')
    return {'evidence': evidence, 'kind': kind, 'identifier': identifier, **resolved}
