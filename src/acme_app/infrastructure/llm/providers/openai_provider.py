"""OpenAI provider.

Same fallback behaviour as the Anthropic adapter: if no key, use the stub.
"""
from __future__ import annotations

import json
import time
from typing import Any

from acme_app.config import settings
from acme_app.infrastructure.llm.providers.anthropic_provider import PLANNER_SYSTEM_PROMPT
from acme_app.infrastructure.llm.providers.base import LLMProvider, LLMResponse
from acme_app.infrastructure.llm.providers.stub_provider import StubProvider, build_plan, narrate


class OpenAIProvider(LLMProvider):
    name = 'openai'

    def __init__(self) -> None:
        self.model = settings.openai_model
        self._fallback = StubProvider()
        self._client = None
        if settings.openai_api_key:
            try:
                import openai  # noqa: WPS433
                self._client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            except Exception:
                self._client = None

    async def plan(self, system_prompt: str, user_prompt: str, context: dict[str, Any]) -> LLMResponse:
        if self._client is None:
            return await self._fallback.plan(system_prompt, user_prompt, context)
        start = time.perf_counter()
        try:
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
                parsed = build_plan(user_prompt, context.get('role', 'sales_user'),
                                    context.get('last_customer'), context.get('last_issue'))
                text_block = json.dumps(parsed)
            usage = resp.usage
            return LLMResponse(
                text=text_block,
                prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
                completion_tokens=getattr(usage, 'completion_tokens', 0) or 0,
                latency_ms=elapsed,
                model=self.model,
                raw=parsed,
            )
        except Exception:
            return await self._fallback.plan(system_prompt, user_prompt, context)

    async def narrate(self, system_prompt: str, user_prompt: str, facts: dict[str, Any]) -> LLMResponse:
        if self._client is None:
            return await self._fallback.narrate(system_prompt, user_prompt, facts)
        start = time.perf_counter()
        try:
            resp = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {'role': 'system', 'content': system_prompt + '\nGround every claim in the provided facts.'},
                    {'role': 'user', 'content': f'{user_prompt}\n\nFacts:\n{json.dumps(facts, default=str)[:6000]}'},
                ],
            )
            elapsed = int((time.perf_counter() - start) * 1000)
            text_block = resp.choices[0].message.content or narrate(facts.get('plan', {}), facts)
            usage = resp.usage
            return LLMResponse(
                text=text_block,
                prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
                completion_tokens=getattr(usage, 'completion_tokens', 0) or 0,
                latency_ms=elapsed,
                model=self.model,
            )
        except Exception:
            return await self._fallback.narrate(system_prompt, user_prompt, facts)
