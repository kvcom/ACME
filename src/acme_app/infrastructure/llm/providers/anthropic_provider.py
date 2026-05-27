from acme_app.infrastructure.llm.providers.base import LLMProvider


class AnthropicProvider(LLMProvider):
    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        customer_name = 'Northwind' if 'northwind' in user_prompt.lower() else 'Acme'
        write_requested = any(t in user_prompt.lower() for t in ['create', 'mark', 'update', 'confirm'])
        return {
            'intent': 'customer_escalation_summary',
            'steps': [
                {
                    'step_type': 'tool',
                    'name': 'search_customers',
                    'arguments': {'customer_name': customer_name},
                    'rationale': 'Resolve customer from query',
                }
            ],
            'write_requested': write_requested,
        }
