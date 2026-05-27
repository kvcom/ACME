from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import HTMLResponse

from acme_app.observability.decision_ledger import TRACE_EVENTS, get_events

router = APIRouter(prefix='/traces', tags=['traces'])


@router.get('')
async def traces() -> dict:
    return {'items': [{'trace_ref': ref, 'event_count': len(events)} for ref, events in TRACE_EVENTS.items()]}


@router.get('/page', response_class=HTMLResponse)
async def traces_page(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse('traces.html', {'request': request})


@router.get('/{trace_ref}', response_class=HTMLResponse)
async def trace_detail(trace_ref: str, request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse('trace_detail.html', {'request': request, 'trace_ref': trace_ref, 'events': get_events(trace_ref)})
