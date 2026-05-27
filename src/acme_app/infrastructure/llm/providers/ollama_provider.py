"""Ollama provider stub.

Documents how a local-model provider would be wired without taking on the
operational risk of running Ollama in the demo container. Exercised by Eval
Case 13 (LLM failure) to prove graceful degradation.
"""
from __future__ import annotations

from typing import Any

from acme_app.infrastructure.llm.providers.base import LLMProvider, LLMResponse


class OllamaProvider(LLMProvider):
    name = 'ollama'
    model = 'ollama-stub'

    async def plan(self, system_prompt: str, user_prompt: str, context: dict[str, Any]) -> LLMResponse:
        raise RuntimeError('LLM unavailable: Ollama provider is stubbed for this prototype')

    async def narrate(self, system_prompt: str, user_prompt: str, facts: dict[str, Any]) -> LLMResponse:
        raise RuntimeError('LLM unavailable: Ollama provider is stubbed for this prototype')
