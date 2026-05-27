from pathlib import Path

from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix='/eval', tags=['eval'])


@router.get('/latest')
async def latest_eval() -> dict:
    p = Path('EVAL_RESULTS.md')
    return {'exists': p.exists(), 'content': p.read_text(encoding='utf-8') if p.exists() else ''}


@router.get('/page', response_class=HTMLResponse)
async def eval_page(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse('eval.html', {'request': request})
