from acme_app.application.prompts import HARDENING_PREAMBLE
from acme_app.application.schemas import AgentPlan
from acme_app.infrastructure.llm.provider import get_provider


async def create_plan(query: str, provider_name: str) -> AgentPlan:
    provider = get_provider(provider_name)
    payload = await provider.complete_json(HARDENING_PREAMBLE, query)
    payload.setdefault('intent', 'customer_query')
    payload.setdefault('steps', [])
    payload.setdefault('write_requested', False)
    return AgentPlan.model_validate(payload)
