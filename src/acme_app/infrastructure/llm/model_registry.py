"""Model registry — the source of truth for what the user can pick from the
composer's model dropdown.

Each entry maps a stable model_key (used as the API parameter) to:
  - provider     — which backend adapter to route through
  - model        — the provider-specific model identifier
  - label        — human-readable name for the UI
  - badge        — short provider chip ("anthropic", "openai", "google", "local", "auto")
  - input_per_1k / output_per_1k — USD cost per 1K tokens
  - visible      — show in the UI dropdown (False = backend-only)

When the user picks a model in the UI, the chat endpoint receives model_key
and resolves it here. The orchestrator never sees provider names directly.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    key: str
    provider: str
    model: str
    label: str
    badge: str
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0
    visible: bool = True


MODEL_REGISTRY: dict[str, ModelSpec] = {
    # Auto — picks the best available model based on availability, cost and locality.
    'auto': ModelSpec(
        key='auto', provider='auto', model='auto-router',
        label='Auto',
        badge='smart',
    ),

    # Anthropic — Claude family.
    'claude-sonnet-4': ModelSpec(
        key='claude-sonnet-4', provider='anthropic', model='claude-sonnet-4-20250514',
        label='Claude Sonnet 4', badge='anthropic',
        input_per_1k=0.003, output_per_1k=0.015,
    ),
    'claude-haiku-4': ModelSpec(
        key='claude-haiku-4', provider='anthropic', model='claude-haiku-4-5-20251001',
        label='Claude Haiku 4.5', badge='anthropic',
        input_per_1k=0.0008, output_per_1k=0.004,
    ),

    # OpenAI — GPT family.
    'gpt-4o': ModelSpec(
        key='gpt-4o', provider='openai', model='gpt-4o',
        label='GPT-4o', badge='openai',
        input_per_1k=0.0025, output_per_1k=0.01,
    ),
    'gpt-4o-mini': ModelSpec(
        key='gpt-4o-mini', provider='openai', model='gpt-4o-mini',
        label='GPT-4o mini', badge='openai',
        input_per_1k=0.00015, output_per_1k=0.0006,
    ),

    # Google — Gemini family.
    'gemini-pro': ModelSpec(
        key='gemini-pro', provider='google', model='gemini-1.5-pro',
        label='Gemini 1.5 Pro', badge='google',
        input_per_1k=0.00125, output_per_1k=0.005,
    ),
    'gemini-flash': ModelSpec(
        key='gemini-flash', provider='google', model='gemini-1.5-flash',
        label='Gemini 1.5 Flash', badge='google',
        input_per_1k=0.000075, output_per_1k=0.0003,
    ),

    # Local — Ollama (no per-token cost).
    'ollama-llama': ModelSpec(
        key='ollama-llama', provider='ollama', model='llama3.1:8b',
        label='Llama 3.1 8B (local)', badge='local',
    ),
    'ollama-qwen': ModelSpec(
        key='ollama-qwen', provider='ollama', model='qwen2.5:7b',
        label='Qwen 2.5 7B (local)', badge='local',
    ),
}


def resolve(model_key: str | None) -> ModelSpec:
    """Look up a model_key, defaulting to Auto when unknown / None."""
    if not model_key:
        return MODEL_REGISTRY['auto']
    return MODEL_REGISTRY.get(model_key, MODEL_REGISTRY['auto'])


def default_key() -> str:
    return 'auto'


def visible_keys() -> list[str]:
    return [k for k, spec in MODEL_REGISTRY.items() if spec.visible]


def visible_registry() -> dict[str, ModelSpec]:
    return {k: spec for k, spec in MODEL_REGISTRY.items() if spec.visible}
