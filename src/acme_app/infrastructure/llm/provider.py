"""Provider factory.

The dropdown picks a model_key; this factory turns that into a provider
instance bound to the specific model. Instances are cached per (provider,
model) pair so a user flipping models in the UI doesn't keep re-instantiating
adapters.
"""
from __future__ import annotations

from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY, ModelSpec, resolve
from acme_app.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from acme_app.infrastructure.llm.providers.base import LLMProvider
from acme_app.infrastructure.llm.providers.google_provider import GoogleProvider
from acme_app.infrastructure.llm.providers.ollama_provider import OllamaProvider
from acme_app.infrastructure.llm.providers.openai_provider import OpenAIProvider
from acme_app.infrastructure.llm.providers.stub_provider import StubProvider


_FACTORIES = {
    'stub': StubProvider,
    'anthropic': AnthropicProvider,
    'openai': OpenAIProvider,
    'google': GoogleProvider,
    'ollama': OllamaProvider,
}

_CACHE: dict[str, LLMProvider] = {}


def _construct(spec: ModelSpec) -> LLMProvider:
    factory = _FACTORIES.get(spec.provider, StubProvider)
    if spec.provider == 'stub':
        return factory()
    try:
        return factory(model=spec.model)
    except TypeError:
        # Older constructor that doesn't accept a model arg.
        inst = factory()
        try:
            inst.model = spec.model
        except Exception:
            pass
        return inst


def get_provider(model_key_or_provider: str | None = None) -> LLMProvider:
    """Resolve a model_key (preferred) or legacy provider name to a provider instance."""
    key = (model_key_or_provider or 'stub').lower()
    # Legacy fallback: callers that still pass a bare provider name get the
    # provider's default model.
    if key in _FACTORIES and key not in MODEL_REGISTRY:
        # Pick the first registry entry matching this provider.
        for spec in MODEL_REGISTRY.values():
            if spec.provider == key:
                key = spec.key
                break
        else:
            key = 'stub'
    spec = resolve(key)
    cache_key = f'{spec.provider}:{spec.model}'
    if cache_key not in _CACHE:
        _CACHE[cache_key] = _construct(spec)
    return _CACHE[cache_key]


def available_providers() -> list[str]:
    return list(_FACTORIES)
