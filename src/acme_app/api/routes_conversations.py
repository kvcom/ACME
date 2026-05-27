from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from fastapi import Request

from acme_app.auth.current_user import CurrentUser, get_current_user

router = APIRouter(prefix='/conversations', tags=['conversations'])


@router.get('')
async def list_conversations(user: CurrentUser = Depends(get_current_user)) -> dict:
    return {'items': [{'conversation_ref': 'CONV-DEMO', 'username': user.username, 'title': 'Northwind escalation', 'message_count': 3}]}


@router.get('/page', response_class=HTMLResponse)
async def conversations_page(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse('conversations.html', {'request': request})
