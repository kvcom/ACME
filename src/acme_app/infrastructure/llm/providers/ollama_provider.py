"""Ollama provider — talks to a local Ollama server over HTTP.

The Ollama API on port 11434 exposes /api/chat which accepts the same
{messages: [...]} shape as OpenAI. We rely on that and ask for JSON-only
responses for the planner.

Reachability:
  - On Docker Desktop (Mac/Windows): http://host.docker.internal:11434 works
    out of the box via the docker-compose extra_hosts mapping.
  - On Linux: set OLLAMA_BASE_URL to http://172.17.0.1:11434.

Gracefully falls back to the stub planner if Ollama is unreachable, so the
demo doesn't break when the host isn't running ollama.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from acme_app.config import settings
from acme_app.infrastructure.llm.providers.anthropic_provider import PLANNER_SYSTEM_PROMPT
from acme_app.infrastructure.llm.providers.base import LLMProvider, LLMResponse
from acme_app.infrastructure.llm.providers.stub_provider import StubProvider, build_plan, narrate


class OllamaProvider(LLMProvider):
    name = 'ollama'

    def __init__(self, model: str | None = None) -> None:
        self.model = model or 'llama3.1:8b'
        self.base_url = settings.ollama_base_url.rstrip('/')
        self._fallback = StubProvider()

    async def _chat(self, system: str, user: str, want_json: bool, max_tokens: int) -> tuple[str, int, int, int]:
        """POST /api/chat and return (text, prompt_tokens, completion_tokens, latency_ms)."""
        start = time.perf_counter()
        payload: dict[str, Any] = {
            'model': self.model,
            'stream': False,
            'options': {'num_predict': max_tokens},
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ],
        }
        if want_json:
            payload['format'] = 'json'
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f'{self.base_url}/api/chat', json=payload)
            response.raise_for_status()
            data = response.json()
        elapsed = int((time.perf_counter() - start) * 1000)
        text = (data.get('message') or {}).get('content', '') or ''
        return text, int(data.get('prompt_eval_count') or 0), int(data.get('eval_count') or 0), elapsed

    async def plan(self, system_prompt: str, user_prompt: str, context: dict[str, Any]) -> LLMResponse:
        try:
            text, pt, ct, elapsed = await self._chat(
                system=PLANNER_SYSTEM_PROMPT + '\n' + system_prompt,
                user=user_prompt,
                want_json=True,
                max_tokens=1024,
            )
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = build_plan(user_prompt, context.get('role', 'sales_user'),
                                    context.get('last_customer'), context.get('last_issue'))
                text = json.dumps(parsed)
            return LLMResponse(
                text=text, prompt_tokens=pt, completion_tokens=ct,
                latency_ms=elapsed, model=self.model, raw=parsed,
            )
        except Exception:
            return await self._fallback.plan(system_prompt, user_prompt, context)

    async def narrate(self, system_prompt: str, user_prompt: str, facts: dict[str, Any]) -> LLMResponse:
        try:
            text, pt, ct, elapsed = await self._chat(
                system=system_prompt + '\nGround every claim in the provided facts.',
                user=f'{user_prompt}\n\nFacts:\n{json.dumps(facts, default=str)[:6000]}',
                want_json=False,
                max_tokens=512,
            )
            if not text.strip():
                text = narrate(facts.get('plan', {}), facts)
            return LLMResponse(
                text=text, prompt_tokens=pt, completion_tokens=ct,
                latency_ms=elapsed, model=self.model,
            )
        except Exception:
            return await self._fallback.narrate(system_prompt, user_prompt, facts)
