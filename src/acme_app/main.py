from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from acme_app.api.routes_actions import router as actions_router
from acme_app.api.routes_auth import router as auth_router
from acme_app.api.routes_chat import router as chat_router
from acme_app.api.routes_conversations import router as conversations_router
from acme_app.api.routes_eval import router as eval_router
from acme_app.api.routes_health import router as health_router
from acme_app.api.routes_traces import router as traces_router
from acme_app.auth.current_user import get_optional_user
from acme_app.observability.otel import setup_otel


logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')

setup_otel()

app = FastAPI(title='Acme Operations Assistant', version='1.0.0')

_static_dir = Path(__file__).parent / 'static'
_templates_dir = Path(__file__).parent / 'templates'
app.mount('/static', StaticFiles(directory=str(_static_dir)), name='static')
app.state.templates = Jinja2Templates(directory=str(_templates_dir))

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(actions_router)
app.include_router(traces_router)
app.include_router(eval_router)


@app.get('/')
async def home(request: Request) -> RedirectResponse:
    user = await get_optional_user(
        authorization=request.headers.get('authorization', ''),
        acme_session=request.cookies.get('acme_session'),
    )
    if user is None:
        return RedirectResponse(url='/login')
    return RedirectResponse(url='/chat')
