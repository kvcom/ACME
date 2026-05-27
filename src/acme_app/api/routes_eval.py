from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from acme_app.auth.current_user import CurrentUser, get_optional_user


router = APIRouter(prefix='/eval', tags=['eval'])


@router.get('', response_class=HTMLResponse)
async def eval_page(
    request: Request,
    user: CurrentUser | None = Depends(get_optional_user),
) -> HTMLResponse:
    p = Path('EVAL_RESULTS.md')
    content = p.read_text(encoding='utf-8') if p.exists() else 'No eval results yet. Run `make eval`.'
    return request.app.state.templates.TemplateResponse(
        request, 'eval.html', {'user': user, 'content': content},
    )


@router.get('/latest')
async def latest_eval() -> dict:
    p = Path('EVAL_RESULTS.md')
    return {'exists': p.exists(), 'content': p.read_text(encoding='utf-8') if p.exists() else ''}
