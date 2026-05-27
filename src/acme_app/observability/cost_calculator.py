from acme_app.infrastructure.llm.cost_table import estimate_cost


def compute(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    return estimate_cost(provider, prompt_tokens, completion_tokens)
