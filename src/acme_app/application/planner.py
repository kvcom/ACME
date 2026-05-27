"""Planner: thin wrapper that asks an LLM provider for a structured plan and
validates the response.

If the provider returns malformed JSON or an unknown tool, we don't reject —
we let the orchestrator handle it (it will log adversarial_block / schema_error
trace events). This keeps the planner pure.
"""
from __future__ import annotations

import json
import time
from typing import Any

from acme_app.application.adversarial import validate_step, validate_step_arguments
from acme_app.application.prompts import HARDENING_PREAMBLE
from acme_app.application.schemas import AgentPlan
from acme_app.infrastructure.llm.provider import get_provider
from acme_app.infrastructure.llm.providers.base import LLMResponse


async def create_plan(query: str, provider_name: str, context: dict[str, Any]) -> tuple[AgentPlan, LLMResponse]:
    provider = get_provider(provider_name)
    start = time.perf_counter()
    response = await provider.plan(HARDENING_PREAMBLE, query, context)
    elapsed = int((time.perf_counter() - start) * 1000)
    response = LLMResponse(
        text=response.text,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        latency_ms=response.latency_ms or elapsed,
        model=response.model,
        raw=response.raw,
    )

    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError:
        payload = {'intent': 'unknown', 'steps': [], 'requires_clarification': True,
                   'clarification_question': 'I could not understand the request.'}

    payload.setdefault('intent', 'unknown')
    payload.setdefault('steps', [])
    payload.setdefault('write_requested', False)
    payload.setdefault('narration_kind', 'general')
    payload.setdefault('adversarial_flags', [])
    payload.setdefault('requires_clarification', False)
    payload.setdefault('clarification_question', None)

    plan = AgentPlan.model_validate(payload)

    cleaned: list[Any] = []
    for step in plan.steps:
        ok_step, _ = validate_step(step.step_type, step.name)
        ok_args, _ = validate_step_arguments(step.name, step.arguments)
        if ok_step and ok_args:
            cleaned.append(step)
    plan.steps = cleaned
    return plan, response
