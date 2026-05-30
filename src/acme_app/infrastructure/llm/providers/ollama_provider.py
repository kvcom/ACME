"""Ollama provider — talks to a local Ollama server over HTTP.

Raises on any failure (server unreachable, model not loaded, etc) so the
caller can decide what to do. With Auto routing, the failure is caught and
the next provider in the chain is tried.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from acme_app.config import settings
from acme_app.infrastructure.llm.providers.anthropic_provider import planner_system_prompt
from acme_app.infrastructure.llm.providers.base import LLMProvider, LLMResponse


class OllamaProvider(LLMProvider):
    name = 'ollama'

    def __init__(self, model: str | None = None) -> None:
        self.model = model or 'qwen3.5:9b'
        self.base_url = settings.ollama_base_url.rstrip('/')
        if not self.base_url:
            raise RuntimeError('OLLAMA_BASE_URL not set')

    def _disable_thinking(self) -> bool:
        return self.model.lower().startswith('qwen3')

    async def _chat(self, system: str, user: str, want_json: bool, max_tokens: int) -> tuple[str, int, int, int]:
        start = time.perf_counter()
        if want_json:
            system = system + '\nReturn JSON only.'
        payload: dict[str, Any] = {
            'model': self.model,
            'stream': False,
            'options': {'num_predict': max_tokens},
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ],
        }
        if self._disable_thinking():
            payload['think'] = False
        if want_json:
            payload['format'] = 'json'
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(f'{self.base_url}/api/chat', json=payload)
            response.raise_for_status()
            data = response.json()
        elapsed = int((time.perf_counter() - start) * 1000)
        text = (data.get('message') or {}).get('content', '') or ''
        return text, int(data.get('prompt_eval_count') or 0), int(data.get('eval_count') or 0), elapsed

    async def plan(self, system_prompt: str, user_prompt: str, context: dict[str, Any]) -> LLMResponse:
        text, pt, ct, elapsed = await self._chat(
            system=planner_system_prompt() + '\n' + system_prompt,
            user=user_prompt,
            want_json=True,
            max_tokens=2048,
        )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {}
        return LLMResponse(
            text=text, prompt_tokens=pt, completion_tokens=ct,
            latency_ms=elapsed, model=self.model, raw=parsed,
        )

    async def narrate(self, system_prompt: str, user_prompt: str, facts: dict[str, Any]) -> LLMResponse:
        text, pt, ct, elapsed = await self._chat(
            system=system_prompt + '\nGround every claim in the provided facts.',
            user=f'{user_prompt}\n\nFacts:\n{json.dumps(facts, default=str)[:6000]}',
            want_json=False,
            max_tokens=2048,
        )
        return LLMResponse(
            text=text, prompt_tokens=pt, completion_tokens=ct,
            latency_ms=elapsed, model=self.model,
        )
