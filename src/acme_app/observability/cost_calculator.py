from acme_app.infrastructure.llm.cost_table import estimate_cost


def compute(model_key: str, prompt_tokens: int, completion_tokens: int) -> float:
    return estimate_cost(model_key, prompt_tokens, completion_tokens)
