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

    # Some providers (notably Ollama with format=json) return explicit nulls for
    # fields the schema expects as strings. Coerce missing-or-null to defaults.
    def _set(key: str, default):
        if payload.get(key) is None:
            payload[key] = default
    _set('intent', 'unknown')
    _set('narration_kind', 'general')
    _set('steps', [])
    _set('adversarial_flags', [])
    _set('write_requested', False)
    _set('requires_clarification', False)
    payload.setdefault('clarification_question', None)
    # If the LLM returned a string for a list field, drop it.
    if not isinstance(payload.get('steps'), list):
        payload['steps'] = []
    if not isinstance(payload.get('adversarial_flags'), list):
        payload['adversarial_flags'] = []

    try:
        plan = AgentPlan.model_validate(payload)
    except Exception:
        # Schema rejected the LLM output entirely — fall back to a no-tools
        # plan so the orchestrator can still produce a polite narration.
        plan = AgentPlan(
            intent=str(payload.get('intent') or 'unknown'),
            requires_clarification=True,
            clarification_question='I had trouble understanding the request.',
            steps=[], write_requested=False, narration_kind='general',
        )

    cleaned: list[Any] = []
    for step in plan.steps:
        ok_step, _ = validate_step(step.step_type, step.name)
        ok_args, _ = validate_step_arguments(step.name, step.arguments)
        if ok_step and ok_args:
            cleaned.append(step)
    plan.steps = cleaned
    return plan, response
