"""OpenAI provider.

Active when OPENAI_API_KEY is set. Raises on any failure — caller decides.
"""
from __future__ import annotations

import json
import time
from typing import Any

from acme_app.config import settings
from acme_app.infrastructure.llm.providers.anthropic_provider import PLANNER_SYSTEM_PROMPT
from acme_app.infrastructure.llm.providers.base import LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    name = 'openai'

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.openai_model
        if not settings.openai_api_key:
            raise RuntimeError('OPENAI_API_KEY not set')
        import openai  # noqa: WPS433
        self._client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    async def plan(self, system_prompt: str, user_prompt: str, context: dict[str, Any]) -> LLMResponse:
        start = time.perf_counter()
        resp = await self._client.chat.completions.create(
            model=self.model,
            response_format={'type': 'json_object'},
            messages=[
                {'role': 'system', 'content': PLANNER_SYSTEM_PROMPT + '\n' + system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        text_block = resp.choices[0].message.content or '{}'
        try:
            parsed = json.loads(text_block)
        except json.JSONDecodeError:
            parsed = {}
        usage = resp.usage
        return LLMResponse(
            text=text_block,
            prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
            completion_tokens=getattr(usage, 'completion_tokens', 0) or 0,
            latency_ms=elapsed,
            model=self.model,
            raw=parsed,
        )

    async def narrate(self, system_prompt: str, user_prompt: str, facts: dict[str, Any]) -> LLMResponse:
        start = time.perf_counter()
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {'role': 'system', 'content': system_prompt + '\nGround every claim in the provided facts.'},
                {'role': 'user', 'content': f'{user_prompt}\n\nFacts:\n{json.dumps(facts, default=str)[:6000]}'},
            ],
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        text_block = resp.choices[0].message.content or ''
        usage = resp.usage
        return LLMResponse(
            text=text_block,
            prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
            completion_tokens=getattr(usage, 'completion_tokens', 0) or 0,
            latency_ms=elapsed,
            model=self.model,
        )
