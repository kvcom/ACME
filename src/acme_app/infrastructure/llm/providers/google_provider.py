"""Google Gemini provider.

Active when GOOGLE_API_KEY is set. Raises on any failure — caller decides.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from acme_app.config import settings
from acme_app.infrastructure.llm.providers.anthropic_provider import planner_system_prompt
from acme_app.infrastructure.llm.providers.base import LLMProvider, LLMResponse


def _parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    match = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', candidate, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
        full_prompt = f'{planner_system_prompt()}\n{system_prompt}\n\nUser query:\n{user_prompt}\n\nRespond with JSON only.'
        response = await self._client.generate_content_async(
            full_prompt,
            generation_config={
                'response_mime_type': 'application/json',
                'max_output_tokens': 4096,
            },
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        text = response.text or '{}'
        parsed = _parse_json_object(text)
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
            generation_config={'max_output_tokens': 2048},
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
