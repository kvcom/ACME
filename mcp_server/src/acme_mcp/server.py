"""Acme MCP Server.

Exposes governed business tools, not raw SQL. Every write is gated by a
confirmation_token verified server-side. See tools.py for the gates.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from acme_mcp import tools


app = FastAPI(title='Acme MCP Server', version='1.0.0')


class SearchCustomersInput(BaseModel):
    customer_name: str = Field(default='', max_length=100)


class CustomerProfileInput(BaseModel):
    customer_name: str = Field(min_length=1, max_length=100)


class OpenIssuesInput(BaseModel):
    customer_id: str | None = Field(default=None, max_length=64)
    customer_name: str | None = Field(default=None, max_length=100)


class IssueRefInput(BaseModel):
    issue_ref: str = Field(min_length=3, max_length=20, pattern=r'^ISS-\d{3,5}$')


class Actor(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    role: str = Field(pattern=r'^(sales_user|support_user|admin)$')


class CreateActionInput(BaseModel):
    actor: Actor
    issue_ref: str = Field(pattern=r'^ISS-\d{3,5}$')
    action_type: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=2000)
    priority: str = Field(pattern=r'^(Low|Medium|High|Critical)$')
    due_at: str | None = Field(default=None, max_length=40)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(min_length=8, max_length=128)
    confirmation_token: str = Field(min_length=8, max_length=512)


class UpdateActionInput(BaseModel):
    actor: Actor
    action_ref: str = Field(pattern=r'^NA-\d+$')
    new_status: str = Field(pattern=r'^(Open|In Progress|Blocked|Completed|Cancelled)$')
    confirmation_token: str = Field(min_length=8, max_length=512)


class UpdateIssueInput(BaseModel):
    actor: Actor
    issue_ref: str = Field(pattern=r'^ISS-\d{3,5}$')
    new_status: str = Field(min_length=1, max_length=40)
    confirmation_token: str = Field(min_length=8, max_length=512)


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/tools')
def list_tools() -> dict[str, list[str]]:
    return {'tools': sorted(_DISPATCH)}


@app.post('/tools/{tool_name}')
def tool_dispatch(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    handler = _DISPATCH.get(tool_name)
    if handler is None:
        raise HTTPException(status_code=404, detail=f'Unknown tool: {tool_name}')
    try:
        return handler(payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'{tool_name} failed: {exc}') from exc


def _search_customers(payload: dict[str, Any]) -> dict[str, Any]:
    args = SearchCustomersInput.model_validate(payload)
    return tools.search_customers(args.customer_name)


def _get_customer_profile(payload: dict[str, Any]) -> dict[str, Any]:
    args = CustomerProfileInput.model_validate(payload)
    return tools.get_customer_profile(args.customer_name)


def _get_open_issues(payload: dict[str, Any]) -> dict[str, Any]:
    args = OpenIssuesInput.model_validate(payload)
    if not args.customer_id and not args.customer_name:
        raise HTTPException(status_code=422, detail='customer_id or customer_name required')
    return tools.get_open_issues(args.customer_id, args.customer_name)


def _summarise_issue_history(payload: dict[str, Any]) -> dict[str, Any]:
    args = IssueRefInput.model_validate(payload)
    return tools.summarise_issue_history(args.issue_ref)


def _recommend_next_action(payload: dict[str, Any]) -> dict[str, Any]:
    args = IssueRefInput.model_validate(payload)
    return tools.recommend_next_action(args.issue_ref)


def _create_next_action(payload: dict[str, Any]) -> dict[str, Any]:
    args = CreateActionInput.model_validate(payload)
    return tools.create_next_action(
        actor=args.actor.model_dump(),
        issue_ref=args.issue_ref,
        action_type=args.action_type,
        title=args.title,
        description=args.description,
        priority=args.priority,
        due_at=args.due_at,
        evidence=args.evidence,
        idempotency_key=args.idempotency_key,
        confirmation_token=args.confirmation_token,
    )


def _update_next_action(payload: dict[str, Any]) -> dict[str, Any]:
    args = UpdateActionInput.model_validate(payload)
    return tools.update_next_action(
        actor=args.actor.model_dump(),
        action_ref=args.action_ref,
        new_status=args.new_status,
        confirmation_token=args.confirmation_token,
    )


def _update_issue_status(payload: dict[str, Any]) -> dict[str, Any]:
    args = UpdateIssueInput.model_validate(payload)
    return tools.update_issue_status(
        actor=args.actor.model_dump(),
        issue_ref=args.issue_ref,
        new_status=args.new_status,
        confirmation_token=args.confirmation_token,
    )


_DISPATCH = {
    'search_customers': _search_customers,
    'get_customer_profile': _get_customer_profile,
    'get_open_issues': _get_open_issues,
    'summarise_issue_history': _summarise_issue_history,
    'recommend_next_action': _recommend_next_action,
    'create_next_action': _create_next_action,
    'update_next_action': _update_next_action,
    'update_issue_status': _update_issue_status,
}


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host='0.0.0.0', port=8001)
