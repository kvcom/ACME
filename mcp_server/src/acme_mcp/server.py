from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from acme_mcp import tools

app = FastAPI(title='Acme MCP Server')


class SearchCustomersInput(BaseModel):
    customer_name: str = Field(min_length=1, max_length=100)


class CustomerProfileInput(BaseModel):
    customer_name: str = Field(min_length=1, max_length=100)


class OpenIssuesInput(BaseModel):
    customer_id: str = Field(min_length=2)


class IssueRefInput(BaseModel):
    issue_ref: str = Field(min_length=3)


class CreateActionInput(BaseModel):
    actor: dict
    issue_ref: str
    action_type: str
    title: str
    description: str
    priority: str
    due_at: str | None = None
    evidence: list[str]
    idempotency_key: str
    confirmation_token: str


class UpdateActionInput(BaseModel):
    actor: dict
    action_ref: str
    new_status: str
    confirmation_token: str


class UpdateIssueInput(BaseModel):
    actor: dict
    issue_ref: str
    new_status: str
    confirmation_token: str


@app.post('/tools/{tool_name}')
async def tool_dispatch(tool_name: str, payload: dict) -> dict:
    if tool_name == 'search_customers':
        return tools.search_customers(SearchCustomersInput.model_validate(payload).customer_name)
    if tool_name == 'get_customer_profile':
        return tools.get_customer_profile(CustomerProfileInput.model_validate(payload).customer_name)
    if tool_name == 'get_open_issues':
        return tools.get_open_issues(OpenIssuesInput.model_validate(payload).customer_id)
    if tool_name == 'summarise_issue_history':
        return tools.summarise_issue_history(IssueRefInput.model_validate(payload).issue_ref)
    if tool_name == 'recommend_next_action':
        return tools.recommend_next_action(IssueRefInput.model_validate(payload).issue_ref)
    if tool_name == 'create_next_action':
        return tools.create_next_action(**CreateActionInput.model_validate(payload).model_dump())
    if tool_name == 'update_next_action':
        return tools.update_next_action(**UpdateActionInput.model_validate(payload).model_dump())
    if tool_name == 'update_issue_status':
        return tools.update_issue_status(**UpdateIssueInput.model_validate(payload).model_dump())
    raise HTTPException(status_code=404, detail=f'Unknown tool: {tool_name}')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8001)
