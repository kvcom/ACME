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
    'claude-opus-4-7': ModelSpec(
        key='claude-opus-4-7', provider='anthropic', model='claude-opus-4-7',
        label='Claude Opus 4.7', badge='anthropic',
        input_per_1k=0.005, output_per_1k=0.025,
    ),
    'claude-sonnet-4-6': ModelSpec(
        key='claude-sonnet-4-6', provider='anthropic', model='claude-sonnet-4-6',
        label='Claude Sonnet 4.6', badge='anthropic',
        input_per_1k=0.003, output_per_1k=0.015,
    ),

    # OpenAI — GPT family.
    'gpt-5.5': ModelSpec(
        key='gpt-5.5', provider='openai', model='gpt-5.5',
        label='GPT-5.5', badge='openai',
        input_per_1k=0.005, output_per_1k=0.03,
    ),
    'gpt-5.4-mini': ModelSpec(
        key='gpt-5.4-mini', provider='openai', model='gpt-5.4-mini',
        label='GPT-5.4 mini', badge='openai',
        input_per_1k=0.00075, output_per_1k=0.0045,
    ),

    # Google — Gemini family.
    'gemini-3.1-pro-preview': ModelSpec(
        key='gemini-3.1-pro-preview', provider='google', model='gemini-3.1-pro-preview',
        label='Gemini 3.1 Pro Preview', badge='google',
        input_per_1k=0.002, output_per_1k=0.012,
    ),
    'gemini-3.5-flash': ModelSpec(
        key='gemini-3.5-flash', provider='google', model='gemini-3.5-flash',
        label='Gemini 3.5 Flash', badge='google',
        input_per_1k=0.0015, output_per_1k=0.009,
    ),

    # Local — Ollama (no per-token cost).
    'ollama-llama': ModelSpec(
        key='ollama-llama', provider='ollama', model='llama3.1:8b',
        label='Llama 3.1 8B (local)', badge='local',
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
