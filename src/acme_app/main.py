from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from acme_app.api.routes_actions import router as actions_router
from acme_app.api.routes_auth import router as auth_router
from acme_app.api.routes_chat import router as chat_router
from acme_app.api.routes_conversations import router as conversations_router
from acme_app.api.routes_eval import router as eval_router
from acme_app.api.routes_health import router as health_router
from acme_app.api.routes_traces import router as traces_router

app = FastAPI(title='Acme Operations Assistant')
app.mount('/static', StaticFiles(directory='src/acme_app/static'), name='static')
app.state.templates = Jinja2Templates(directory='src/acme_app/templates')

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(actions_router)
app.include_router(traces_router)
app.include_router(eval_router)


@app.get('/', response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return app.state.templates.TemplateResponse('chat.html', {'request': request, 'user': {'username': 'guest', 'roles': ['sales_user']}, 'provider': 'anthropic', 'conversation_ref': 'CONV-DEMO'})
