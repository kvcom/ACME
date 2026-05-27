"""LLM provider protocol.

Two operations: planning (structured JSON plan) and narration (free-form final
answer grounded in retrieved facts). Token + latency accounting is the
provider's responsibility.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    model: str = ''
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    name: str = 'base'
    model: str = 'unknown'

    @abstractmethod
    async def plan(self, system_prompt: str, user_prompt: str, context: dict[str, Any]) -> LLMResponse:
        """Return a JSON-encoded plan in LLMResponse.text."""

    @abstractmethod
    async def narrate(self, system_prompt: str, user_prompt: str, facts: dict[str, Any]) -> LLMResponse:
        """Return free-text grounded answer in LLMResponse.text."""
