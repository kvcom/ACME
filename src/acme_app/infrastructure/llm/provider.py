"""Provider factory.

The dropdown picks a model_key; this factory turns that into a provider
instance bound to the specific model. Instances are cached per (provider,
model) pair so a user flipping models in the UI doesn't keep re-instantiating
adapters.
"""
from __future__ import annotations

from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY, ModelSpec, resolve
from acme_app.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from acme_app.infrastructure.llm.providers.auto_provider import AutoProvider
from acme_app.infrastructure.llm.providers.base import LLMProvider
from acme_app.infrastructure.llm.providers.google_provider import GoogleProvider
from acme_app.infrastructure.llm.providers.ollama_provider import OllamaProvider
from acme_app.infrastructure.llm.providers.openai_provider import OpenAIProvider


_FACTORIES = {
    'auto': AutoProvider,
    'anthropic': AnthropicProvider,
    'openai': OpenAIProvider,
    'google': GoogleProvider,
    'ollama': OllamaProvider,
}

_CACHE: dict[str, LLMProvider] = {}


def _construct(spec: ModelSpec) -> LLMProvider:
    factory = _FACTORIES[spec.provider]
    if spec.provider == 'auto':
        return factory()
    return factory(model=spec.model)


def get_provider(model_key_or_provider: str | None = None) -> LLMProvider:
    """Resolve a model_key (preferred) or legacy provider name to a provider instance.

    Returns AutoProvider for unknown keys or 'auto'. Real providers raise
    RuntimeError at construction if their credentials are missing — Auto wraps
    that into chain fallback; an explicit pick surfaces the error.
    """
    key = (model_key_or_provider or 'auto').lower()
    # Legacy fallback: callers passing a bare provider name get its first model.
    if key in _FACTORIES and key not in MODEL_REGISTRY:
        for k, spec in MODEL_REGISTRY.items():
            if spec.provider == key:
                key = k
                break
        else:
            key = 'auto'
    spec = resolve(key)
    cache_key = f'{spec.provider}:{spec.model}'
    if cache_key not in _CACHE:
        _CACHE[cache_key] = _construct(spec)
    return _CACHE[cache_key]


def available_providers() -> list[str]:
    return list(_FACTORIES)
