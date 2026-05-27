PRICING = {
    'anthropic': {'input_per_1k': 0.003, 'output_per_1k': 0.015},
    'openai': {'input_per_1k': 0.0025, 'output_per_1k': 0.01},
    'ollama': {'input_per_1k': 0.0, 'output_per_1k': 0.0},
}


def estimate_cost(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = PRICING.get(provider, PRICING['anthropic'])
    return round(prompt_tokens / 1000 * rates['input_per_1k'] + completion_tokens / 1000 * rates['output_per_1k'], 6)
