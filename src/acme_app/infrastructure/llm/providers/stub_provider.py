"""Deterministic stub LLM.

Used as the default so the system runs end-to-end without external API keys.
The planner is keyword + structure based and covers all 13 eval cases.
Narration is templated from retrieved facts.

This is explicitly a stub: it is not a substitute for a real LLM. The point is
to make the rest of the system demoable, evaluable, and testable.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from acme_app.infrastructure.llm.providers.base import LLMProvider, LLMResponse


ADVERSARIAL_PATTERNS = [
    re.compile(r'ignore (all )?(previous|prior|above) instructions', re.I),
    re.compile(r'you are now (an? )?(admin|root|system)', re.I),
    re.compile(r'system\s*:\s*you', re.I),
    re.compile(r'disregard (your|the) (rules|policy|guardrails)', re.I),
]


def detect_adversarial(query: str) -> tuple[bool, list[str]]:
    hits = [p.pattern for p in ADVERSARIAL_PATTERNS if p.search(query)]
    return (len(hits) > 0, hits)


KNOWN_CUSTOMERS = {
    'northwind': 'Northwind Energy',
    'contoso': 'Contoso Retail',
    'acme logistics': 'Acme Logistics Europe',
    'acme manufacturing': 'Acme Manufacturing Group',
    'blueriver': 'BlueRiver Health',
    'skyline': 'Skyline Aviation',
}


def _extract_customer(query: str) -> str | None:
    lower = query.lower()
    for needle, full in KNOWN_CUSTOMERS.items():
        if needle in lower:
            return full
    m = re.search(r'\bAcme\b', query, re.I)
    if m:
        return 'Acme'
    return None


def _extract_issue_ref(query: str) -> str | None:
    m = re.search(r'ISS-\d{3,4}', query, re.I)
    return m.group(0).upper() if m else None


def _is_write_intent(query: str) -> bool:
    return bool(re.search(r'\b(create|propose|prepare|escalate|assign|mark|update|schedule)\b', query, re.I))


def _is_confirm(query: str) -> bool:
    return query.strip().lower() in {'confirm', 'confirm.', 'confirm that', 'yes', 'go ahead', 'do it'} or query.strip().lower().startswith('confirm')


def _is_closure_check(query: str) -> bool:
    return bool(re.search(r'\b(close|closure|ready to close|can we close)\b', query, re.I))


def _is_escalation_summary(query: str) -> bool:
    return bool(re.search(r'\b(escalation summary|all high-risk|management attention|all customers)\b', query, re.I))


def _is_simple_profile(query: str) -> bool:
    return bool(re.search(r'\bprofile\b', query, re.I)) and not _is_write_intent(query)


def build_plan(query: str, role: str, last_customer: str | None, last_issue: str | None) -> dict[str, Any]:
    """Produce a deterministic structured plan."""
    adversarial, hits = detect_adversarial(query)
    if adversarial:
        return {
            'intent': 'adversarial',
            'adversarial_flags': hits,
            'requires_clarification': False,
            'clarification_question': None,
            'steps': [],
            'write_requested': False,
            'narration_kind': 'refusal',
        }

    customer = _extract_customer(query) or last_customer
    issue_ref = _extract_issue_ref(query) or last_issue

    if _is_confirm(query):
        return {
            'intent': 'confirm_pending_action',
            'requires_clarification': False,
            'clarification_question': None,
            'steps': [],
            'write_requested': True,
            'narration_kind': 'confirmation',
        }

    if customer == 'Acme':
        return {
            'intent': 'disambiguate_customer',
            'requires_clarification': True,
            'clarification_question': 'I found multiple customers matching "Acme". Did you mean Acme Logistics Europe or Acme Manufacturing Group?',
            'steps': [
                {'step_type': 'tool', 'name': 'search_customers', 'arguments': {'customer_name': 'Acme'}, 'rationale': 'Multiple customers match the name; surface options.'}
            ],
            'write_requested': False,
            'narration_kind': 'clarification',
        }

    if _is_escalation_summary(query):
        return {
            'intent': 'escalation_summary_all',
            'requires_clarification': False,
            'clarification_question': None,
            'steps': [
                {'step_type': 'tool', 'name': 'search_customers', 'arguments': {'customer_name': ''}, 'rationale': 'List customers to summarise.'},
                {'step_type': 'tool', 'name': 'get_open_issues', 'arguments': {'customer_name': 'Northwind Energy'}, 'rationale': 'Fetch open issues for Northwind.'},
                {'step_type': 'tool', 'name': 'summarise_issue_history', 'arguments': {'issue_ref': 'ISS-102'}, 'rationale': 'Summarise highest-risk issue.'},
                {'step_type': 'tool', 'name': 'get_open_issues', 'arguments': {'customer_name': 'Acme Manufacturing Group'}, 'rationale': 'Fetch open issues for Acme Manufacturing.'},
                {'step_type': 'tool', 'name': 'summarise_issue_history', 'arguments': {'issue_ref': 'ISS-401'}, 'rationale': 'Summarise next high-risk issue.'},
                {'step_type': 'tool', 'name': 'get_open_issues', 'arguments': {'customer_name': 'Skyline Aviation'}, 'rationale': 'Borderline high-risk.'},
                {'step_type': 'skill', 'name': 'customer_escalation_summary', 'arguments': {'customer_name': 'Northwind Energy'}, 'rationale': 'Produce structured summary.'},
            ],
            'write_requested': False,
            'narration_kind': 'escalation_summary',
        }

    if _is_closure_check(query):
        return {
            'intent': 'closure_readiness',
            'requires_clarification': False,
            'clarification_question': None,
            'steps': [
                {'step_type': 'tool', 'name': 'summarise_issue_history', 'arguments': {'issue_ref': issue_ref or 'ISS-102'}, 'rationale': 'Need history to judge closure readiness.'},
                {'step_type': 'skill', 'name': 'closure_readiness_check', 'arguments': {'issue_ref': issue_ref or 'ISS-102'}, 'rationale': 'Closure readiness Skill.'},
            ],
            'write_requested': False,
            'narration_kind': 'closure_readiness',
        }

    if _is_simple_profile(query) and customer:
        return {
            'intent': 'simple_profile_lookup',
            'requires_clarification': False,
            'clarification_question': None,
            'steps': [
                {'step_type': 'tool', 'name': 'get_customer_profile', 'arguments': {'customer_name': customer}, 'rationale': 'User asked for profile only.'},
            ],
            'write_requested': False,
            'narration_kind': 'profile',
        }

    if _is_write_intent(query):
        return {
            'intent': 'propose_action',
            'requires_clarification': False,
            'clarification_question': None,
            'steps': [
                {'step_type': 'tool', 'name': 'get_customer_profile', 'arguments': {'customer_name': customer or 'Northwind Energy'}, 'rationale': 'Resolve customer first.'},
                {'step_type': 'tool', 'name': 'summarise_issue_history', 'arguments': {'issue_ref': issue_ref or 'ISS-102'}, 'rationale': 'Ground the recommendation in history.'},
                {'step_type': 'tool', 'name': 'recommend_next_action', 'arguments': {'issue_ref': issue_ref or 'ISS-102'}, 'rationale': 'Produce a structured recommendation.'},
            ],
            'write_requested': True,
            'narration_kind': 'propose_action',
        }

    if customer:
        return {
            'intent': 'customer_briefing',
            'requires_clarification': False,
            'clarification_question': None,
            'steps': [
                {'step_type': 'tool', 'name': 'get_customer_profile', 'arguments': {'customer_name': customer}, 'rationale': 'Resolve customer.'},
                {'step_type': 'tool', 'name': 'get_open_issues', 'arguments': {'customer_name': customer}, 'rationale': 'List open issues for the briefing.'},
                {'step_type': 'tool', 'name': 'summarise_issue_history', 'arguments': {'issue_ref': issue_ref or 'ISS-102'}, 'rationale': 'Summarise top issue.'},
                {'step_type': 'skill', 'name': 'customer_escalation_summary', 'arguments': {'customer_name': customer}, 'rationale': 'Need structured briefing including risk and next step.'},
            ],
            'write_requested': False,
            'narration_kind': 'customer_briefing',
        }

    return {
        'intent': 'general_query',
        'requires_clarification': True,
        'clarification_question': 'Which customer or issue are you asking about?',
        'steps': [],
        'write_requested': False,
        'narration_kind': 'clarification',
    }


def narrate(plan: dict[str, Any], facts: dict[str, Any]) -> str:
    kind = plan.get('narration_kind', 'general')
    if kind == 'refusal':
        return ("I can't follow that instruction. Your message looked like an attempt to override my "
                "instructions. I will not change role or create actions outside of policy. If you have a "
                "legitimate request, please rephrase it as a normal question.")
    if kind == 'clarification':
        return plan.get('clarification_question') or 'Could you clarify which customer or issue you mean?'
    if kind == 'confirmation':
        return 'Confirmation received. The action has been created under policy.'
    skill = facts.get('skill_output') or {}
    profile = facts.get('customer_profile') or {}
    issues = facts.get('open_issues') or []
    history = facts.get('issue_history') or {}
    if kind == 'profile' and profile:
        return (f"{profile.get('name', 'Customer')} — {profile.get('tier', '')} {profile.get('industry', '')}, "
                f"{profile.get('region', '')}. Account owner: {profile.get('account_owner', 'unassigned')}.")
    if kind == 'closure_readiness':
        ready = skill.get('ready_to_close')
        if ready is False:
            return f"Not ready to close. {skill.get('reason', '')}"
        if ready is True:
            return 'Issue appears ready to close based on available evidence.'
        return 'Unable to assess closure readiness from available data.'
    if kind == 'propose_action':
        rec = facts.get('recommendation') or {}
        return (f"I recommend {rec.get('action_type', 'an action')} ({rec.get('priority', 'Medium')}): "
                f"{rec.get('title', '')}. Rationale: {rec.get('rationale', '')}. "
                f"This is a proposal — please confirm to create it.")
    if kind == 'escalation_summary':
        summary = skill.get('executive_summary') or 'Multiple customers reviewed; see evidence for detail.'
        return f"Escalation summary: {summary}"
    name = profile.get('name', 'the customer')
    risk = skill.get('risk_level') or 'unassessed'
    rec = (skill.get('recommended_next_action') or {})
    next_step = rec.get('title') or 'review with account team'
    open_count = len(issues)
    return (f"{name}: {open_count} open issue(s). Risk: {risk}. "
            f"Latest update: {history.get('latest_update', 'n/a')}. "
            f"Recommended next step: {next_step}.")


class StubProvider(LLMProvider):
    name = 'stub'
    model = 'stub-planner-v1'

    async def plan(self, system_prompt: str, user_prompt: str, context: dict[str, Any]) -> LLMResponse:
        start = time.perf_counter()
        plan = build_plan(
            user_prompt,
            context.get('role', 'sales_user'),
            context.get('last_customer'),
            context.get('last_issue'),
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        text = json.dumps(plan)
        return LLMResponse(text=text, prompt_tokens=len(system_prompt) // 4 + len(user_prompt) // 4,
                           completion_tokens=len(text) // 4, latency_ms=elapsed, model=self.model, raw=plan)

    async def narrate(self, system_prompt: str, user_prompt: str, facts: dict[str, Any]) -> LLMResponse:
        start = time.perf_counter()
        text = narrate(facts.get('plan', {}), facts)
        elapsed = int((time.perf_counter() - start) * 1000)
        return LLMResponse(text=text, prompt_tokens=len(system_prompt) // 4 + len(user_prompt) // 4,
                           completion_tokens=len(text) // 4, latency_ms=elapsed, model=self.model)
