"""Provider factory.

Cached singletons per provider name; switching providers mid-process is allowed
(used by the X-LLM-Provider header in /chat).
"""
from __future__ import annotations

from acme_app.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from acme_app.infrastructure.llm.providers.base import LLMProvider
from acme_app.infrastructure.llm.providers.ollama_provider import OllamaProvider
from acme_app.infrastructure.llm.providers.openai_provider import OpenAIProvider
from acme_app.infrastructure.llm.providers.stub_provider import StubProvider

_PROVIDER_FACTORIES: dict[str, type[LLMProvider]] = {
    'stub': StubProvider,
    'anthropic': AnthropicProvider,
    'openai': OpenAIProvider,
    'ollama': OllamaProvider,
}

_CACHE: dict[str, LLMProvider] = {}


def get_provider(name: str | None = None) -> LLMProvider:
    key = (name or 'stub').lower()
    if key not in _CACHE:
        factory = _PROVIDER_FACTORIES.get(key, StubProvider)
        _CACHE[key] = factory()
    return _CACHE[key]


def available_providers() -> list[str]:
    return list(_PROVIDER_FACTORIES)
