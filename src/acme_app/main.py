from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from acme_app.api.routes_actions import router as actions_router
from acme_app.api.routes_auth import router as auth_router
from acme_app.api.routes_chat import router as chat_router
from acme_app.api.routes_conversations import router as conversations_router
from acme_app.api.routes_db_explorer import router as db_explorer_router
from acme_app.api.routes_eval import router as eval_router
from acme_app.api.routes_evidence import router as evidence_router
from acme_app.api.routes_health import router as health_router
from acme_app.api.routes_traces import router as traces_router
from acme_app.application.realtime import broadcaster as realtime_broadcaster
from acme_app.auth.current_user import get_optional_user
from acme_app.observability.otel import setup_otel


logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')

setup_otel()

app = FastAPI(title='Acme Operations Assistant', version='1.0.0')


@app.on_event('startup')
async def _start_realtime() -> None:
    await realtime_broadcaster.start()


@app.on_event('shutdown')
async def _stop_realtime() -> None:
    await realtime_broadcaster.stop()

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
app.include_router(evidence_router)
app.include_router(eval_router)
app.include_router(db_explorer_router)


# HTML routes (the browser ones) should redirect to /login on 401, not return
# raw JSON. We detect "HTML request" by checking the Accept header — XHR/JSON
# clients (the eval runner, fetch() in chat.js) keep the JSON response so they
# can react in code.
_HTML_PATH_PREFIXES = ('/chat', '/conversations', '/traces', '/eval', '/db-explorer', '/login', '/')


def _wants_html(request: Request) -> bool:
    accept = request.headers.get('accept', '')
    if 'text/html' in accept:
        return True
    if accept.startswith('*/*') and not request.url.path.startswith(('/auth/', '/actions/', '/health', '/ready')):
        # Browsers that omit accept on navigation still want HTML.
        return any(request.url.path.startswith(p) for p in _HTML_PATH_PREFIXES)
    return False


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and _wants_html(request):
        return RedirectResponse(url=f'/login?next={request.url.path}', status_code=303)
    return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})


@app.get('/')
async def home(request: Request) -> RedirectResponse:
    user = await get_optional_user(
        authorization=request.headers.get('authorization', ''),
        acme_session=request.cookies.get('acme_session'),
    )
    if user is None:
        return RedirectResponse(url='/login')
    return RedirectResponse(url='/chat')
