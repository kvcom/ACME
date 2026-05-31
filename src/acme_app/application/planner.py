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
from acme_app.application.schemas import AgentPlan, PlanStep
from acme_app.infrastructure.llm.provider import get_provider
from acme_app.infrastructure.llm.providers.base import LLMResponse


def _fallback_intent(query: str, steps: list[Any], write_requested: bool) -> str:
    """Infer a stable UI intent when a model leaves intent blank/unknown."""
    if write_requested:
        return 'write_action'
    step_names = {getattr(step, 'name', '') for step in steps}
    q = query.lower()
    if 'get_open_issues' in step_names:
        return 'customer_status'
    if 'get_customer_profile' in step_names:
        return 'customer_profile'
    if 'summarise_issue_history' in step_names:
        return 'issue_summary'
    if 'recommend_next_action' in step_names:
        return 'recommendation'
    if 'closure_readiness_check' in step_names or 'close' in q or 'closure' in q:
        return 'closure_check'
    if steps:
        return 'planned_lookup'
    return 'unknown'


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

    if isinstance(response.raw, dict) and response.raw:
        payload = response.raw
    else:
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
    _set('answer_scope', 'auto')
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
    seen_steps: set[tuple[str, str, str]] = set()
    customer_profile_names: set[str] = set()
    open_issue_names: set[str] = set()
    answer_scope = str(getattr(plan, 'answer_scope', '') or '').strip().lower()
    for step in plan.steps:
        ok_step, _ = validate_step(step.step_type, step.name)
        ok_args, _ = validate_step_arguments(step.name, step.arguments)
        if ok_step and ok_args:
            if answer_scope == 'profile' and step.name != 'get_customer_profile':
                continue
            if answer_scope == 'status' and step.name == 'customer_escalation_summary':
                continue
            customer_name = str(step.arguments.get('customer_name') or '').strip().lower()
            if step.name == 'customer_escalation_summary':
                if not customer_name or customer_name not in customer_profile_names or customer_name not in open_issue_names:
                    continue
            step_key = (step.step_type, step.name, json.dumps(step.arguments, sort_keys=True))
            if step_key in seen_steps:
                continue
            seen_steps.add(step_key)
            cleaned.append(step)
            if step.name == 'get_customer_profile' and customer_name:
                customer_profile_names.add(customer_name)
            elif step.name == 'get_open_issues' and customer_name:
                open_issue_names.add(customer_name)
    plan.steps = cleaned
    if plan.intent in {'', 'unknown', 'null', 'none'}:
        plan.intent = _fallback_intent(query, plan.steps, plan.write_requested)
    return plan, response
