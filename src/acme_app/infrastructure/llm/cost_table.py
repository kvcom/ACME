"""Per-model USD pricing.

Single source of truth lives in MODEL_REGISTRY — this module just adapts it
to a callable shape that the orchestrator already uses.
"""
from __future__ import annotations

from acme_app.infrastructure.llm.model_registry import resolve


def estimate_cost(model_key: str, prompt_tokens: int, completion_tokens: int) -> float:
    spec = resolve(model_key)
    cost = (prompt_tokens / 1000) * spec.input_per_1k + (completion_tokens / 1000) * spec.output_per_1k
    return round(cost, 6)


# Legacy compatibility dict so existing imports (PRICING['anthropic']) still work.
PRICING = {
    'stub': {'input_per_1k': 0.0, 'output_per_1k': 0.0},
    'anthropic': {'input_per_1k': 0.003, 'output_per_1k': 0.015},
    'openai': {'input_per_1k': 0.0025, 'output_per_1k': 0.01},
    'google': {'input_per_1k': 0.00125, 'output_per_1k': 0.005},
    'ollama': {'input_per_1k': 0.0, 'output_per_1k': 0.0},
}
