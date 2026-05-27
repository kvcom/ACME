"""Per-provider USD pricing. Used by observability.cost_calculator."""
from __future__ import annotations

PRICING: dict[str, dict[str, float]] = {
    'stub': {'input_per_1k': 0.0, 'output_per_1k': 0.0},
    'anthropic': {'input_per_1k': 0.003, 'output_per_1k': 0.015},
    'openai': {'input_per_1k': 0.0025, 'output_per_1k': 0.01},
    'ollama': {'input_per_1k': 0.0, 'output_per_1k': 0.0},
}


def estimate_cost(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = PRICING.get(provider.lower(), PRICING['stub'])
    return round(prompt_tokens / 1000 * rates['input_per_1k'] + completion_tokens / 1000 * rates['output_per_1k'], 6)
