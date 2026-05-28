"""Google Gemini provider.

Active when GOOGLE_API_KEY is set. Raises on any failure — caller decides.
"""
from __future__ import annotations

import json
import time
from typing import Any

from acme_app.config import settings
from acme_app.infrastructure.llm.providers.anthropic_provider import PLANNER_SYSTEM_PROMPT
from acme_app.infrastructure.llm.providers.base import LLMProvider, LLMResponse


class GoogleProvider(LLMProvider):
    name = 'google'

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.google_model
        if not settings.google_api_key:
            raise RuntimeError('GOOGLE_API_KEY not set')
        import google.generativeai as genai  # noqa: WPS433
        genai.configure(api_key=settings.google_api_key)
        self._client = genai.GenerativeModel(self.model)

    async def plan(self, system_prompt: str, user_prompt: str, context: dict[str, Any]) -> LLMResponse:
        start = time.perf_counter()
        full_prompt = f'{PLANNER_SYSTEM_PROMPT}\n{system_prompt}\n\nUser query:\n{user_prompt}\n\nRespond with JSON only.'
        response = await self._client.generate_content_async(
            full_prompt,
            generation_config={
                'response_mime_type': 'application/json',
                'max_output_tokens': 1024,
            },
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        text = response.text or '{}'
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {}
        usage = getattr(response, 'usage_metadata', None)
        return LLMResponse(
            text=text,
            prompt_tokens=getattr(usage, 'prompt_token_count', 0) or 0,
            completion_tokens=getattr(usage, 'candidates_token_count', 0) or 0,
            latency_ms=elapsed,
            model=self.model,
            raw=parsed,
        )

    async def narrate(self, system_prompt: str, user_prompt: str, facts: dict[str, Any]) -> LLMResponse:
        start = time.perf_counter()
        full_prompt = (
            f'{system_prompt}\nGround every claim in the provided facts.\n\n'
            f'User question:\n{user_prompt}\n\nFacts:\n{json.dumps(facts, default=str)[:6000]}'
        )
        response = await self._client.generate_content_async(
            full_prompt,
            generation_config={'max_output_tokens': 512},
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        text = (response.text or '').strip()
        usage = getattr(response, 'usage_metadata', None)
        return LLMResponse(
            text=text,
            prompt_tokens=getattr(usage, 'prompt_token_count', 0) or 0,
            completion_tokens=getattr(usage, 'candidates_token_count', 0) or 0,
            latency_ms=elapsed,
            model=self.model,
        )
