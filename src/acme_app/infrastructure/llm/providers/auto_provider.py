"""Auto provider.

Picks the best available model from a priority chain and falls back through
the chain on failure. Selection is based on:

    1. Availability — is the API key / Ollama URL configured?
    2. Cost        — cheaper models tried first
    3. Locality    — local Ollama tried before any cloud (no rate limits, no $)

The chain is fixed and ordered:

    ollama-qwen   →  local, free, fast
    ollama-llama  →  local, free
    gemini-flash  →  cheapest cloud ($0.000075 in / $0.0003 out per 1K)
    gpt-4o-mini   →  next cheapest
    claude-haiku-4
    gemini-pro
    gpt-4o
    claude-sonnet-4

The actually-used model is returned in LLMResponse.model so the trace viewer
shows which one ran. If nothing is available we raise LLMUnavailable, which
the orchestrator catches and surfaces as the "LLM Unavailable" badge.
"""
from __future__ import annotations

import logging
from typing import Any

from acme_app.config import settings
from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY, ModelSpec
from acme_app.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from acme_app.infrastructure.llm.providers.base import LLMProvider, LLMResponse
from acme_app.infrastructure.llm.providers.google_provider import GoogleProvider
from acme_app.infrastructure.llm.providers.ollama_provider import OllamaProvider
from acme_app.infrastructure.llm.providers.openai_provider import OpenAIProvider


_log = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Raised when no provider in the auto chain succeeded for a request."""


PRIORITY_CHAIN: list[str] = [
    'ollama-qwen',
    'ollama-llama',
    'gemini-flash',
    'gpt-4o-mini',
    'claude-haiku-4',
    'gemini-pro',
    'gpt-4o',
    'claude-sonnet-4',
]


def _is_available(spec: ModelSpec) -> bool:
    if spec.provider == 'ollama':
        # We can't ping cheaply at startup without slowing boot, so we keep
        # Ollama in the chain when a base URL is configured. The HTTP call will
        # fail fast (~3s) if Ollama isn't up, and we'll move to the next.
        return bool(settings.ollama_base_url)
    if spec.provider == 'anthropic':
        return bool(settings.anthropic_api_key)
    if spec.provider == 'openai':
        return bool(settings.openai_api_key)
    if spec.provider == 'google':
        return bool(settings.google_api_key)
    return False


def _construct(spec: ModelSpec) -> LLMProvider:
    cls_map: dict[str, type[LLMProvider]] = {
        'anthropic': AnthropicProvider,
        'openai':    OpenAIProvider,
        'google':    GoogleProvider,
        'ollama':    OllamaProvider,
    }
    cls = cls_map[spec.provider]
    return cls(model=spec.model)  # type: ignore[call-arg]


class AutoProvider(LLMProvider):
    name = 'auto'

    def __init__(self) -> None:
        self._chain: list[ModelSpec] = [
            MODEL_REGISTRY[key] for key in PRIORITY_CHAIN
            if key in MODEL_REGISTRY and _is_available(MODEL_REGISTRY[key])
        ]
        self.model = self._chain[0].key if self._chain else 'auto-no-model'

    def available_chain(self) -> list[str]:
        return [spec.key for spec in self._chain]

    async def _try_chain(self, op_name: str, fn_args: tuple) -> LLMResponse:
        if not self._chain:
            raise LLMUnavailableError(
                'No LLM is available. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, '
                'GOOGLE_API_KEY in .env, or start a local Ollama server.'
            )
        last_error: Exception | None = None
        for spec in self._chain:
            try:
                provider = _construct(spec)
                method = getattr(provider, op_name)
                response: LLMResponse = await method(*fn_args)
                # Treat empty text as a soft failure and try the next provider.
                if not (response.text or '').strip():
                    raise RuntimeError(f'{spec.key} returned empty {op_name}')
                _log.info('Auto: %s succeeded with %s', op_name, spec.key)
                return response
            except Exception as exc:
                last_error = exc
                _log.warning('Auto: %s failed via %s (%s); trying next',
                             op_name, spec.key, type(exc).__name__)
                continue
        raise LLMUnavailableError(
            f'Auto: all {len(self._chain)} providers failed for {op_name}. '
            f'Last error: {last_error}'
        )

    async def plan(self, system_prompt: str, user_prompt: str, context: dict[str, Any]) -> LLMResponse:
        return await self._try_chain('plan', (system_prompt, user_prompt, context))

    async def narrate(self, system_prompt: str, user_prompt: str, facts: dict[str, Any]) -> LLMResponse:
        return await self._try_chain('narrate', (system_prompt, user_prompt, facts))
