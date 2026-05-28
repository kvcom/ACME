"""Planner: thin wrapper that asks an LLM provider for a structured plan and
validates the response.

If the provider returns malformed JSON or an unknown tool, we don't reject —
we let the orchestrator handle it (it will log adversarial_block / schema_error
trace events). This keeps the planner pure.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from acme_app.application.adversarial import validate_step, validate_step_arguments
from acme_app.application.prompts import HARDENING_PREAMBLE
from acme_app.application.schemas import AgentPlan, PlanStep
from acme_app.infrastructure.llm.provider import get_provider
from acme_app.infrastructure.llm.providers.base import LLMResponse


_CUSTOMER_STATUS_RE = re.compile(
    r'\b(open issues|latest status|status|recommended next step|next step|brief|call with)\b',
    re.I,
)
_CUSTOMER_HINT_PATTERNS = (
    re.compile(r'\bcall with\s+([A-Z][A-Za-z0-9 &.-]+?)(?:\s+today|[.?!,]|$)', re.I),
    re.compile(r'\b(?:for|about|with)\s+([A-Z][A-Za-z0-9 &.-]+?)(?:\s+issue\b|\s+today|[.?!,]|$)', re.I),
)


def _extract_customer_hint(query: str) -> str | None:
    for pattern in _CUSTOMER_HINT_PATTERNS:
        match = pattern.search(query)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


def _deterministic_fallback_steps(query: str) -> tuple[str, list[PlanStep]]:
    """Provide stable tool plans for obvious workflows when a small model drifts."""
    customer = _extract_customer_hint(query)
    if customer and _CUSTOMER_STATUS_RE.search(query):
        return 'customer_status', [
            PlanStep(
                step_type='tool',
                name='get_customer_profile',
                arguments={'customer_name': customer},
                rationale='Fetch customer profile for the requested briefing.',
            ),
            PlanStep(
                step_type='tool',
                name='get_open_issues',
                arguments={'customer_name': customer},
                rationale='Retrieve open issues and current statuses for the customer.',
            ),
            PlanStep(
                step_type='skill',
                name='customer_escalation_summary',
                arguments={'customer_name': customer},
                rationale='Summarise customer risk and recommended next action.',
            ),
        ]
    return 'unknown', []


def _merge_required_steps(existing: list[PlanStep], required: list[PlanStep]) -> list[PlanStep]:
    """Append required deterministic steps that the model omitted."""
    merged = list(existing)
    seen = {
        (step.step_type, step.name, json.dumps(step.arguments, sort_keys=True))
        for step in merged
    }
    for step in required:
        step_key = (step.step_type, step.name, json.dumps(step.arguments, sort_keys=True))
        if step_key not in seen:
            seen.add(step_key)
            merged.append(step)
    return merged


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
    for step in plan.steps:
        ok_step, _ = validate_step(step.step_type, step.name)
        ok_args, _ = validate_step_arguments(step.name, step.arguments)
        if ok_step and ok_args:
            step_key = (step.step_type, step.name, json.dumps(step.arguments, sort_keys=True))
            if step_key in seen_steps:
                continue
            seen_steps.add(step_key)
            cleaned.append(step)
    plan.steps = cleaned
    if not plan.write_requested:
        fallback_intent, fallback_steps = _deterministic_fallback_steps(query)
        if fallback_steps:
            plan.intent = fallback_intent if plan.intent in {'', 'unknown', 'null', 'none', 'clarify'} else plan.intent
            plan.steps = _merge_required_steps(plan.steps, fallback_steps)
            plan.requires_clarification = False
            plan.clarification_question = None
    if plan.intent in {'', 'unknown', 'null', 'none'}:
        plan.intent = _fallback_intent(query, plan.steps, plan.write_requested)
    return plan, response
