"""Agent orchestrator.

The execution loop in section 12.3 of the plan, condensed:

  1. adversarial check (deterministic rules + optional local-LLM second opinion;
     OR-combined so either source can flag)
  2. PII redaction (regex rules + optional local-LLM substring suggestions,
     applied as a union)
  3. Redis context load (with PostgreSQL fallback when Redis has expired)
  4. LLM plan
  5. Validate plan; reject unknown tools/skills/action_types
  6. Execute tool steps via MCP; record tool_call_logs
  7. Execute skill steps in-process; record events
  8. If write_requested: pre-check RBAC, then stage a Proposed action (never auto-create)
  9. LLM narration grounded in retrieved facts
 10. Persist trace + events + RBAC decisions to PostgreSQL
 11. Stream a structured ChatResponse back to the caller

The orchestrator also emits streaming events via an optional event_sink callable,
used by the SSE endpoint. When event_sink is None (eval, tests) it is a no-op.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.application.adversarial import (
    check_query,
    pattern_matches,
    validate_step,
    validate_step_arguments,
)
from acme_app.application.outbound_privacy import (
    OutboundPrivacyContext,
    build_privacy_context,
    privacy_diff,
    privacy_manifest,
    restore_text_from_llm,
    sanitize_facts_for_llm,
    sanitize_text_for_llm,
    sanitize_text_with_report,
    translate_customer_args_to_names,
)
from acme_app.application.planner import create_plan
from acme_app.application.prompts import NARRATION_PREAMBLE
from acme_app.application.propose_confirm import (
    build_proposed_action,
    get_pending_action,
    stage_pending_action,
)
from acme_app.application.schemas import (
    ChatResponse,
    ClarificationOptionDTO,
    ProposedActionDTO,
    ResolutionOptionDTO,
    ResolutionRequiredDTO,
)
from acme_app.domain.evidence import badge_for
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY
from acme_app.infrastructure.llm.provider import get_provider
from acme_app.infrastructure.llm.providers.auto_provider import (
    ROUTE_CHAINS,
    RouteDecision,
)
from acme_app.infrastructure.llm.providers.base import LLMResponse
from acme_app.infrastructure.mcp_client.client import MCPClient, MCPClientError
from acme_app.infrastructure.mcp_client.schemas import WRITE_TOOLS
from acme_app.infrastructure.redis_memory import conversation_memory
from acme_app.observability import decision_ledger as ledger_mod
from acme_app.observability.cost_calculator import compute as compute_cost
from acme_app.observability.otel import current_trace_id_hex, get_tracer
from acme_app.policy.action_guard import can_propose, verify_confirmation_token
from acme_app.policy.local_screener import (
    apply_extra_redactions,
    llm_adversarial_flags,
    llm_pii_substrings,
    local_screener_available,
)
from acme_app.policy.pii_redactor import redact, redaction_report
from acme_app.skills.registry import SKILLS

_log = logging.getLogger(__name__)
EventSink = Callable[[str, dict[str, Any]], Awaitable[None]] | None


async def _detect_customer_ambiguity_preplan(
    session: AsyncSession, user_query: str,
) -> dict[str, Any] | None:
    """Check the user's raw query against the customers table BEFORE the LLM
    plans anything.

    Returns an ambiguous_customer dict (queried + matches) if the query
    mentions a customer-name stem (e.g. "Acme") that matches multiple
    customers, AND the user did NOT include any disambiguating word —
    a non-stem token from one specific candidate's name, or that candidate's
    region. Otherwise returns None.

    This sits in front of the planner so the LLM can't silently pre-resolve
    an ambiguous reference by stuffing a guessed full name into tool args.
    """
    if not user_query:
        return None
    rows = (await session.execute(text(
        "SELECT name, tier, region FROM customers"
    ))).all()
    customers = [{'name': r[0], 'tier': r[1], 'region': r[2]} for r in rows]
    q = user_query.lower()

    # 1) Did the user explicitly include any customer's full name?
    #    If yes, no ambiguity — they were specific.
    if any(c['name'].lower() in q for c in customers):
        return None

    # 2) Group customers by the first token of their name. Stems that map to
    #    >1 customer are the candidates for ambiguity.
    stem_groups: dict[str, list[dict[str, Any]]] = {}
    for c in customers:
        first = c['name'].split()[0].lower()
        stem_groups.setdefault(first, []).append(c)

    for stem, group in stem_groups.items():
        if len(group) <= 1:
            continue
        if not re.search(r'\b' + re.escape(stem) + r'\b', q):
            continue
        # The user said the stem. Did they also say something that picks one?
        disambiguated_candidates: list[dict[str, Any]] = []
        for c in group:
            # Non-stem tokens from this customer's name (e.g. for "Acme
            # Manufacturing Group" → ["manufacturing", "group"])
            extra_tokens = [
                t.lower() for t in c['name'].split()
                if t.lower() != stem and len(t) > 3
            ]
            region = (c.get('region') or '').lower()
            tier = (c.get('tier') or '').lower()
            if any(t in q for t in extra_tokens):
                disambiguated_candidates.append(c)
            elif region and re.search(r'\b' + re.escape(region) + r'\b', q):
                disambiguated_candidates.append(c)
            elif tier and tier in q:
                disambiguated_candidates.append(c)
        if len(disambiguated_candidates) == 1:
            return None  # User disambiguated via region / extra token / tier
        # Either no disambiguator, or it disambiguates to more than one →
        # still ambiguous.
        return {
            'queried': stem.title(),
            'matches': group,
        }

    return None

# Customers we know about, used to recover a "last customer in scope" hint when
# Redis has expired and we're rebuilding context from the durable PG history.
_KNOWN_CUSTOMERS = (
    'Northwind Energy', 'Contoso Retail',
    'Acme Logistics Europe', 'Acme Manufacturing Group',
    'BlueRiver Health', 'Skyline Aviation',
)
_ISSUE_REF_RE = re.compile(r'\bISS-\d{3,5}\b', re.I)
_WRITE_INTENT_RE = re.compile(
    r'\b('
    r'create|prepare|propose|draft|stage|schedule|assign|'
    r'add|make|submit|write up|set up|mark|escalate'
    r')\b',
    re.I,
)
_OPEN_WRITE_INTENT_RE = re.compile(
    r'\b(open|file|log|raise)\s+(an?\s+)?'
    r'(action|ticket|case|issue|task|next action|recovery plan)\b',
    re.I,
)

_CUSTOMER_ALIASES = {
    'northwind': 'Northwind Energy',
    'contoso': 'Contoso Retail',
    'blueriver': 'BlueRiver Health',
    'blue river': 'BlueRiver Health',
    'skyline': 'Skyline Aviation',
    'acme logistics': 'Acme Logistics Europe',
    'acme manufacturing': 'Acme Manufacturing Group',
}


_EXTERNAL_PRIVACY_INSTRUCTION = (
    'Privacy boundary: some names have been replaced with opaque internal record '
    'tokens before this prompt was sent to you. For planning, copy any customer '
    'or user record token from the request exactly when a tool argument needs it. '
    'In final answers, never mention privacy mode, record tokens, database IDs, '
    'or ask the user to provide internal IDs; write naturally using the facts given.\n\n'
)


def _with_external_privacy_instruction(prompt: str) -> str:
    return _EXTERNAL_PRIVACY_INSTRUCTION + prompt


async def _load_recent_turns_with_pg_fallback(
    session: AsyncSession,
    username: str,
    conversation_ref: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
    """Get recent turns + best-effort (last_customer, last_issue).

    Redis is the fast path. If it has expired (TTL 30 min) we recover from
    PostgreSQL — agent_traces is the durable record. We also write what we
    find back into Redis so the next turn in this conversation skips the
    PG roundtrip again.
    """
    turns = await conversation_memory.get_context(username, conversation_ref)
    last_customer = await conversation_memory.get_last_customer(username, conversation_ref)
    last_issue = await conversation_memory.get_last_issue(username, conversation_ref)

    if not turns:
        pg_history = await repo.get_conversation_history(session, conversation_ref)
        if pg_history:
            for h in pg_history[-3:]:
                turns.append({'role': 'user', 'text': h.get('user_query') or ''})
                turns.append({'role': 'assistant',
                              'text': h.get('answer') or '',
                              'trace_ref': h.get('trace_ref')})
            # Backfill Redis so subsequent turns within this session are fast.
            for turn in turns:
                await conversation_memory.append_context(username, conversation_ref, turn)

    if (not last_customer or not last_issue) and turns:
        for turn in reversed(turns):
            text = turn.get('text') or ''
            if not last_issue:
                m = _ISSUE_REF_RE.search(text)
                if m:
                    last_issue = m.group(0).upper()
                    await conversation_memory.set_last_issue(username, conversation_ref, last_issue)
            if not last_customer:
                for c in _KNOWN_CUSTOMERS:
                    if c.lower() in text.lower():
                        last_customer = {'name': c}
                        await conversation_memory.set_last_customer(username, conversation_ref, last_customer)
                        break
            if last_customer and last_issue:
                break

    return turns, last_customer, last_issue


def _recent_customer_scope(
    recent_turns: list[dict[str, Any]],
    privacy: OutboundPrivacyContext,
) -> list[str]:
    """Find customer names the user recently put in scope.

    User messages are treated as more authoritative than assistant text so an
    earlier bad answer cannot easily expand the scope with the wrong customer.
    """
    if not recent_turns:
        return []

    def collect(turns: list[dict[str, Any]]) -> list[str]:
        found: list[str] = []
        text = '\n'.join(str(turn.get('text') or '') for turn in turns)
        for customer in privacy.customers:
            if any(re.search(r'\b' + re.escape(alias) + r'\b', text, flags=re.I) for alias in customer.aliases):
                found.append(customer.name)
        return found

    user_found = collect([turn for turn in recent_turns[-8:] if turn.get('role') == 'user'])
    if len(user_found) > 1:
        return user_found

    combined = collect(recent_turns[-6:])
    return combined


def _recent_issue_scope(recent_turns: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    for turn in recent_turns[-6:]:
        for match in _ISSUE_REF_RE.finditer(str(turn.get('text') or '')):
            ref = match.group(0).upper()
            if ref not in refs:
                refs.append(ref)
    return refs[:8]


def _new_trace_ref() -> str:
    return f'TRC-{uuid.uuid4().hex[:8].upper()}'


def _summarise(output: dict[str, Any]) -> dict[str, Any]:
    return ledger_mod.summarise_output(output)


def _evidence_from_tool_output(tool_name: str, output: dict[str, Any]) -> list[str]:
    if tool_name == 'get_customer_profile' and output.get('customer_id'):
        return [f'customer:{output["customer_id"]}']
    if tool_name == 'get_open_issues':
        return [
            f'issue:{issue["issue_ref"]}'
            for issue in output.get('issues', [])
            if isinstance(issue, dict) and issue.get('issue_ref')
        ]
    if tool_name in {'summarise_issue_history', 'recommend_next_action'}:
        return list(output.get('evidence') or [])
    return []


def _customer_fact_key(arguments: dict[str, Any], output: dict[str, Any] | None = None) -> str:
    output = output or {}
    return str(
        output.get('name')
        or arguments.get('customer_name')
        or arguments.get('customer_id')
        or 'unknown_customer'
    ).strip()


def _ensure_customer_fact(facts: dict[str, Any], key: str) -> dict[str, Any]:
    customers = facts.setdefault('customer_facts', {})
    if not isinstance(customers, dict):
        customers = {}
        facts['customer_facts'] = customers
    return customers.setdefault(key, {'customer_name': key})


def _comparison_customers(facts: dict[str, Any]) -> list[dict[str, Any]]:
    customers = facts.get('customer_facts')
    if not isinstance(customers, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key, fact in customers.items():
        if not isinstance(fact, dict):
            continue
        profile = fact.get('customer_profile') or {}
        issues = fact.get('open_issues') or []
        skill_output = fact.get('skill_output') or {}
        if profile or issues or skill_output:
            rows.append({
                'customer_name': profile.get('name') or key,
                'profile': profile,
                'open_issues': issues,
                'risk_level': skill_output.get('risk_level'),
                'risk_factors': skill_output.get('risk_factors', []),
                'recommended_next_action': skill_output.get('recommended_next_action'),
                'executive_summary': skill_output.get('executive_summary'),
                'missing_information': skill_output.get('missing_information', []),
            })
    return rows


def _explicit_write_intent(query: str) -> bool:
    """Only stage proposed actions when the user's wording asks for a write."""
    return bool(_WRITE_INTENT_RE.search(query) or _OPEN_WRITE_INTENT_RE.search(query))


def _explicit_escalation_intent(query: str) -> bool:
    return bool(re.search(r'\b(mark|escalate|escalated|escalation)\b', query, re.I))


def _looks_like_confirmation(query: str) -> bool:
    """Return True for short, explicit confirmations of a staged action."""
    short_affirmations = {
        'yes', 'yeah', 'yep', 'sure', 'ok', 'okay', 'confirm',
        'go ahead', 'do it', 'approve', 'create it', 'create',
        'proceed', 'please confirm',
    }
    normalised = query.strip().lower().rstrip('.!')
    return (
        '?' not in query
        and (
            normalised in short_affirmations
            or normalised.startswith('confirm')
            or normalised.startswith('yes ')
            or len(normalised.split()) <= 3
        )
    )


def _customer_from_text(query: str, last_customer: dict[str, Any] | None = None) -> str | None:
    text = query.lower()
    for alias, name in _CUSTOMER_ALIASES.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', text):
            return name
    if last_customer and last_customer.get('name'):
        return str(last_customer['name'])
    return None


def _first_issue_ref(query: str, last_issue: str | None = None) -> str | None:
    match = _ISSUE_REF_RE.search(query)
    if match:
        return match.group(0).upper()
    return last_issue


def _has_step(plan: Any, name: str) -> bool:
    return any(getattr(step, 'name', '') == name for step in getattr(plan, 'steps', []))


def _append_step(plan: Any, step_type: str, name: str, arguments: dict[str, Any], rationale: str) -> None:
    if _has_step(plan, name) and name not in {'summarise_issue_history', 'recommend_next_action'}:
        return
    step_key = (step_type, name, json.dumps(arguments, sort_keys=True))
    for existing in plan.steps:
        existing_key = (
            existing.step_type,
            existing.name,
            json.dumps(existing.arguments, sort_keys=True),
        )
        if existing_key == step_key:
            return
    from acme_app.application.schemas import PlanStep
    plan.steps.append(PlanStep(
        step_type=step_type,
        name=name,
        arguments=arguments,
        rationale=rationale,
    ))


def _stabilise_plan_for_supported_workflows(
    *,
    plan: Any,
    query: str,
    effective_query: str,
    last_customer: dict[str, Any] | None,
    last_issue: str | None,
    ledger: ledger_mod.Ledger,
) -> None:
    """Add deterministic guardrail steps for well-known business workflows.

    The LLM still chooses and narrates the workflow. These additions make the
    execution layer refuse under-instrumented plans for common requests where
    the product contract requires evidence, issue history, or a recommendation.
    """
    q = f'{query}\n{effective_query}'.lower()
    customer = _customer_from_text(effective_query, last_customer)
    issue_ref = _first_issue_ref(effective_query, last_issue)
    before = [(s.step_type, s.name, dict(s.arguments)) for s in plan.steps]

    asks_customer_status = any(
        phrase in q for phrase in (
            'open issues', 'latest status', 'call with', 'brief me',
            'escalation summary', 'high-risk customers', 'management attention',
            'what should we do next', 'recommended next step',
        )
    )
    asks_recommendation = any(
        phrase in q for phrase in ('recommended next step', 'what should we do next', 'next action')
    )
    asks_closure = 'close ' in q or 'closure' in q or 'ready to close' in q

    if asks_customer_status and customer:
        _append_step(plan, 'tool', 'get_customer_profile', {'customer_name': customer}, 'resolve customer profile')
        _append_step(plan, 'tool', 'get_open_issues', {'customer_name': customer}, 'retrieve open issues')
        _append_step(plan, 'skill', 'customer_escalation_summary', {'customer_name': customer}, 'summarise escalation risk')

    if 'high-risk customers' in q or 'management attention' in q:
        portfolio_customer = customer or 'Northwind Energy'
        _append_step(plan, 'tool', 'get_customer_profile', {'customer_name': portfolio_customer}, 'resolve high-risk customer profile')
        _append_step(plan, 'tool', 'get_open_issues', {'customer_name': portfolio_customer}, 'retrieve high-risk customer issues')
        _append_step(plan, 'skill', 'customer_escalation_summary', {'customer_name': portfolio_customer}, 'summarise management attention')

    if asks_closure and issue_ref:
        _append_step(plan, 'tool', 'summarise_issue_history', {'issue_ref': issue_ref}, 'retrieve issue history')
        _append_step(plan, 'skill', 'closure_readiness_check', {'issue_ref': issue_ref}, 'check closure readiness')

    if _explicit_write_intent(effective_query) and issue_ref:
        _append_step(plan, 'tool', 'summarise_issue_history', {'issue_ref': issue_ref}, 'retrieve write target evidence')
        _append_step(plan, 'tool', 'recommend_next_action', {'issue_ref': issue_ref}, 'choose governed next action')
        plan.write_requested = True

    if asks_recommendation and issue_ref:
        _append_step(plan, 'tool', 'recommend_next_action', {'issue_ref': issue_ref}, 'choose governed next action')

    after = [(s.step_type, s.name, dict(s.arguments)) for s in plan.steps]
    if after != before:
        ledger.event('agent_plan', 'plan.guardrail_enriched', {
            'before': [name for _, name, _ in before],
            'after': [name for _, name, _ in after],
        })


def _proposal_denied_answer(role: str, action_type: str, reason: str) -> str:
    action_label = action_type.replace('_', ' ').title() if action_type else 'this action'
    return (
        f'Permission denied: your role `{role}` can read the evidence and recommend next steps, '
        f'but it cannot create `{action_type}`.\n\n'
        f'Nothing was staged or created. Ask a support user or admin to create the '
        f'{action_label.lower()}, or continue using the recommendation as read-only guidance.\n\n'
        f'Policy reason: {reason}'
    )


def _model_key_for_cost(model_name: str, fallback_key: str) -> str:
    for key, spec in MODEL_REGISTRY.items():
        if spec.model == model_name or spec.key == model_name:
            return key
    return fallback_key


def _external_llm_used(plan_model: str, narration_model: str, route_source: str | None) -> bool:
    model_used = any(
        model and not model.startswith(('llama', 'qwen'))
        for model in (plan_model, narration_model)
    )
    classifier_used = False
    if route_source and route_source.startswith(('arbiter:', 'model:')):
        classifier_key = route_source.split(':', 1)[1]
        classifier_used = not classifier_key.startswith('ollama-')
    return model_used or classifier_used


def _parse_json_object(text_value: str) -> dict[str, Any]:
    candidate = (text_value or '').strip()
    match = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', candidate, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _valid_route(route: str) -> str:
    return route if route in ROUTE_CHAINS else 'clarification'


def _resolution_payload(rules: RouteDecision, model: RouteDecision) -> ResolutionRequiredDTO:
    rules_option = ResolutionOptionDTO(
        key='rules',
        label=f'Use rules: {rules.route}',
        route=rules.route,
        reason=rules.reason,
    )
    model_option = ResolutionOptionDTO(
        key='model',
        label=f'Use model: {model.route}',
        route=model.route,
        reason=model.reason,
    )
    other_option = ResolutionOptionDTO(
        key='other',
        label='Other / clarify',
        route='clarification',
        reason='Ask the user to clarify the intended workflow.',
    )
    return ResolutionRequiredDTO(
        title='Classification conflict',
        message='The deterministic rules and the selected model disagree about how to handle this request.',
        rules=rules_option,
        model=model_option,
        options=[rules_option, model_option, other_option],
    )


def _customer_clarification_options(matches: list[dict[str, Any]]) -> list[ClarificationOptionDTO]:
    options: list[ClarificationOptionDTO] = []
    for match in matches:
        name = str(match.get('name') or '').strip()
        if not name:
            continue
        detail = ' · '.join(str(v) for v in (match.get('tier'), match.get('region')) if v)
        options.append(ClarificationOptionDTO(label=name, value=name, description=detail))
    return options


def _clarification_followup_query(query: str, recent_turns: list[dict[str, Any]]) -> str | None:
    """Expand a short clarification answer into the original unresolved request.

    Small local models often treat a clarification answer like "Acme
    Manufacturing Group" or "ISS-102" as the whole request. This helper
    preserves the user's choice while making the intended prior question
    explicit, including write requests that first needed an issue/customer.
    """
    choice = query.strip()
    if not choice or '?' in choice or len(choice.split()) > 6:
        return None

    previous_user: str | None = None
    clarification_kind: str | None = None
    saw_customer_clarification = False
    saw_action_target_clarification = False
    for turn in reversed(recent_turns[-6:]):
        text = (turn.get('text') or '').strip()
        if not text:
            continue
        if turn.get('role') == 'assistant' and (
            'which one did you mean' in text.lower()
            or 'multiple customers match' in text.lower()
            or 'found 2 customers in scope' in text.lower()
        ):
            saw_customer_clarification = True
            clarification_kind = 'customer'
            continue
        if turn.get('role') == 'assistant' and (
            'which customer or issue' in text.lower()
            or 'which issue' in text.lower()
            or 'provide a customer name or an issue reference' in text.lower()
            or 'provide the issue reference' in text.lower()
        ):
            saw_action_target_clarification = True
            clarification_kind = 'action_target'
            continue
        if (saw_customer_clarification or saw_action_target_clarification) and turn.get('role') == 'user':
            previous_user = text
            break

    if not previous_user:
        return None

    if clarification_kind == 'action_target':
        return (
            f'The user provided "{choice}" as the issue/customer target for their previous request: '
            f'"{previous_user}". Preserve that original request, including any write intent, '
            f'and use "{choice}" as the target identifier.'
        )

    return (
        f'The user selected customer "{choice}" to answer their previous request: '
        f'"{previous_user}". Use "{choice}" as the customer_name for the plan.'
    )


async def _emit(sink: EventSink, name: str, payload: dict[str, Any]) -> None:
    if sink is None:
        return
    try:
        await sink(name, payload)
    except Exception:
        _log.exception('event sink failure')


async def run_agent(
    *,
    session: AsyncSession,
    query: str,
    username: str,
    role: str,
    conversation_ref: str,
    provider_name: str,
    event_sink: EventSink = None,
    mcp_client: MCPClient | None = None,
    resolution_route: str | None = None,
) -> ChatResponse:
    mcp = mcp_client or MCPClient()
    trace_ref = _new_trace_ref()
    ledger = ledger_mod.Ledger(trace_ref=trace_ref, username=username, user_role=role)
    started_ms = int(time.time() * 1000)

    tracer = get_tracer()
    with tracer.start_as_current_span('agent.request') as span:
        span.set_attribute('user.role', role)
        span.set_attribute('llm.provider', provider_name)

        ok_length, rules_adversarial, rules_flags = check_query(query)
        ledger.event('auth', 'auth.validate_role', {'role': role, 'username': username})

        # Stages 2 + 3 — adversarial and PII — run the deterministic rules
        # AND, when a local Ollama model is available, a second-opinion LLM
        # screener in parallel. Both stages fail soft: if the local model is
        # offline or returns garbage, the orchestrator behaves exactly as
        # before (rules-only). When the local model IS available:
        #   adversarial → flag if rules OR local model flag it (union of reasons)
        #   pii.redact  → apply rules first, then redact additional substrings
        #                 the local model surfaced (regex + LLM complementary)
        local_available = local_screener_available() and ok_length
        if local_available:
            llm_adv_task = asyncio.create_task(llm_adversarial_flags(query))
            llm_pii_task = asyncio.create_task(llm_pii_substrings(query))
            llm_adv_flags = await llm_adv_task
            llm_pii_subs = await llm_pii_task
        else:
            llm_adv_flags = None
            llm_pii_subs = None

        adversarial = rules_adversarial or bool(llm_adv_flags)
        flags = list(rules_flags)
        if llm_adv_flags:
            flags.extend(llm_adv_flags)
        adv_contributors = ['rules']
        if llm_adv_flags is not None:
            adv_contributors.append('local_llm')

        ledger.event('adversarial', 'adversarial.check',
                     {'flags': flags,
                      'length_ok': ok_length,
                      'detected': adversarial,
                      'contributors': adv_contributors,
                      'rules_flags': rules_flags,
                      'rule_matches': pattern_matches(query),
                      'llm_flags': llm_adv_flags or [],
                      'llm_checked': llm_adv_flags is not None,
                      'message_length': len(query)},
                     status='blocked' if (adversarial or not ok_length) else 'ok')
        await _emit(event_sink, 'adversarial', {'flags': flags, 'detected': adversarial})

        rules_redacted = redact(query)
        query_redacted = (
            apply_extra_redactions(rules_redacted, llm_pii_subs)
            if llm_pii_subs else rules_redacted
        )
        pii_contributors = ['rules']
        if llm_pii_subs is not None:
            pii_contributors.append('local_llm')
        ledger.event('pii', 'pii.redact',
                     {'changed': query_redacted != query,
                      'length': len(query),
                      'rules_changed': rules_redacted != query,
                      'rules_redactions': redaction_report(query),
                      'llm_added_spans': len(llm_pii_subs or []),
                      'llm_substrings_redacted': [
                          {'length': len(s), 'replacement': '[REDACTED-LLM]'}
                          for s in (llm_pii_subs or [])
                      ],
                      'contributors': pii_contributors})

        privacy = build_privacy_context(
            model_key_or_provider=provider_name,
            customers=await repo.list_customers(session),
            users=await repo.list_users(session),
            pii_substrings=llm_pii_subs,
        )

        if not ok_length:
            return await _finalise_blocked(
                session, ledger, query, query_redacted, provider_name,
                conversation_ref, badge='Adversarial Input Blocked',
                answer='Your query exceeded the maximum length.',
                intent='blocked_length', started_ms=started_ms,
            )

        recent_turns, last_customer, last_issue = await _load_recent_turns_with_pg_fallback(
            session, username, conversation_ref,
        )
        pending = await get_pending_action(conversation_ref)
        if pending:
            ledger.event('memory', 'redis.pending_action_present', {'present': True})
        if recent_turns:
            ledger.event('memory', 'history.loaded',
                         {'turns': len(recent_turns),
                          'last_customer': (last_customer or {}).get('name'),
                          'last_issue': last_issue,
                          'loaded_turns': [
                              {
                                  'role': turn.get('role'),
                                  'text': redact(turn.get('text') or '')[:300],
                                  'trace_ref': turn.get('trace_ref'),
                              }
                              for turn in recent_turns[-6:]
                          ]})

        # Build a transcript snippet for the LLM so short follow-ups ("yes",
        # "that one", pronouns) resolve against the actual previous turn.
        history_text = ''
        recent_customers = _recent_customer_scope(recent_turns, privacy)
        recent_issues = _recent_issue_scope(recent_turns)
        if recent_turns:
            lines = []
            for turn in recent_turns[-6:]:
                who = 'User' if turn.get('role') == 'user' else 'Assistant'
                txt = (turn.get('text') or '').strip().replace('\n', ' ')
                if txt:
                    lines.append(f'{who}: {txt[:300]}')
            if lines:
                history_text = 'Recent conversation:\n' + '\n'.join(lines) + '\n\n'
        if len(recent_customers) > 1:
            history_text += f'Recent customers in scope: {", ".join(recent_customers)}\n'
        elif last_customer and last_customer.get('name'):
            history_text += f'Last customer in scope: {last_customer["name"]}\n'
        if len(recent_issues) > 1:
            history_text += f'Recent issues in scope: {", ".join(recent_issues)}\n'
        elif last_issue and len(recent_customers) <= 1:
            history_text += f'Last issue in scope: {last_issue}\n'
        if pending:
            history_text += (
                f'There is a pending proposed action awaiting user confirmation: '
                f'{pending.get("action_type")} on {pending.get("issue_ref")}.\n'
            )
        effective_query = _clarification_followup_query(query, recent_turns) or query
        enriched_query = (history_text + '\nCurrent message:\n' + effective_query) if history_text else effective_query
        llm_sanitized = sanitize_text_with_report(enriched_query, privacy) if privacy.external else None
        llm_enriched_query = llm_sanitized.text if llm_sanitized else enriched_query
        trace_enriched_query = restore_text_from_llm(
            sanitize_text_for_llm(enriched_query, privacy),
            privacy,
        )
        if privacy.external:
            ledger.event(
                'privacy',
                'outbound_llm.minimized',
                {
                    'external_model': provider_name,
                    'customer_names_replaced_with_ids': len(llm_sanitized.customer_replacements),
                    'user_names_replaced_with_ids': len(llm_sanitized.user_replacements),
                    'pii_redaction_applied': query_redacted != query or bool(llm_sanitized.pii_redactions),
                    **privacy_manifest(privacy, applied=llm_sanitized),
                },
            )
            llm_enriched_query = _with_external_privacy_instruction(llm_enriched_query)
        if history_text or effective_query != query:
            ledger.event('memory', 'context.built_for_planner',
                         {'history_context': trace_enriched_query.strip(),
                          'effective_query': restore_text_from_llm(
                              sanitize_text_for_llm(effective_query, privacy),
                              privacy,
                          ),
                          'planner_input': trace_enriched_query[:3000],
                          'outbound_planner_input': llm_enriched_query[:3000] if privacy.external else None})

        if pending and _looks_like_confirmation(query):
            return await _confirm_pending(
                session=session, ledger=ledger, mcp=mcp,
                username=username, role=role, conversation_ref=conversation_ref,
                query=query, query_redacted=query_redacted,
                provider_name=provider_name,
                llm_plan_response=LLMResponse(
                    text='{"intent":"confirm_pending_action","steps":[],"write_requested":true}',
                    model='deterministic-confirm',
                    raw={
                        'intent': 'confirm_pending_action',
                        'steps': [],
                        'write_requested': True,
                    },
                ),
                started_ms=started_ms, event_sink=event_sink,
            )

        plan_context = {
            'role': role,
            'last_customer': (last_customer or {}).get('name') if last_customer else None,
            'last_issue': last_issue,
            'recent_customers': recent_customers,
            'recent_issues': recent_issues,
        }
        route_decision: RouteDecision | None = None

        # Pre-plan ambiguity gate (runs BEFORE the LLM gets to plan):
        # if the raw user query references a customer-name stem that's
        # ambiguous across multiple known customers, and contains no
        # disambiguator, refuse to let the LLM plan tool calls. Otherwise
        # the LLM might pre-resolve the ambiguity by stuffing one full name
        # into the args (GPT-4o-mini pattern) or drop tokens (Llama 7B
        # pattern). Short-circuit to a clarification narration.
        preplan_ambig = await _detect_customer_ambiguity_preplan(session, query)
        if preplan_ambig:
            ledger.event('agent_plan', 'ambiguous_customer.preplan_detected',
                         {'queried': preplan_ambig['queried'],
                          'candidates': [m['name'] for m in preplan_ambig['matches']]})
            await _emit(event_sink, 'plan',
                        {'intent': 'disambiguate_customer',
                         'steps_count': 0,
                         'narration_kind': 'clarification'})
            return await _handle_preplan_ambiguity(
                session=session, ledger=ledger,
                username=username, role=role, conversation_ref=conversation_ref,
                query=query, query_redacted=query_redacted,
                provider_name=provider_name, started_ms=started_ms,
                event_sink=event_sink, history_text=history_text,
                ambiguity=preplan_ambig, privacy=privacy,
            )

        with tracer.start_as_current_span('agent.plan'):
            try:
                if resolution_route:
                    route_decision = RouteDecision(
                        route=_valid_route(resolution_route),
                        confidence=1.0,
                        reason='human-selected resolution',
                        source='human',
                    )
                    plan_context['human_resolution_route'] = route_decision.route
                    enriched_query = (
                        f'Human classification resolution: handle this request as route '
                        f'"{route_decision.route}".\n\n{enriched_query}'
                    )
                    llm_sanitized = sanitize_text_with_report(enriched_query, privacy) if privacy.external else None
                    llm_enriched_query = llm_sanitized.text if llm_sanitized else enriched_query
                    trace_enriched_query = restore_text_from_llm(
                        sanitize_text_for_llm(enriched_query, privacy),
                        privacy,
                    )
                    if privacy.external:
                        llm_enriched_query = (
                            _with_external_privacy_instruction(llm_enriched_query)
                        )
                    ledger.event('agent_plan', 'classification.human_resolved',
                                 {'route': route_decision.route})
                plan, llm_plan_response = await create_plan(
                    llm_enriched_query, provider_name,
                    context=plan_context,
                )
                if privacy.external:
                    translate_customer_args_to_names(plan, privacy)
            except Exception as exc:
                ledger.event('error', 'llm.unavailable', {'error': str(exc)}, status='error')
                await _emit(event_sink, 'llm_unavailable', {'error': str(exc)})
                return await _finalise_blocked(
                    session, ledger, query, query_redacted, provider_name,
                    conversation_ref, badge='LLM Unavailable',
                    answer=(
                        "I couldn't reach any LLM. Please configure ANTHROPIC_API_KEY, "
                        "OPENAI_API_KEY, or GOOGLE_API_KEY in .env, or start a local "
                        "Ollama server. Detail: " + str(exc)[:200]
                    ),
                    intent='llm_unavailable', started_ms=started_ms,
                )
        plan_event_model_key = _model_key_for_cost(llm_plan_response.model or '', provider_name)
        plan_event_cost = compute_cost(
            plan_event_model_key,
            llm_plan_response.prompt_tokens,
            llm_plan_response.completion_tokens,
        )
        ledger.event('agent_plan', 'plan.created',
                     {'intent': plan.intent, 'steps': len(plan.steps), 'write_requested': plan.write_requested,
                      'narration_kind': plan.narration_kind,
                      'requires_clarification': plan.requires_clarification,
                      'clarification_question': plan.clarification_question,
                      'planned_steps': [
                          {
                              'step_type': step.step_type,
                              'name': step.name,
                              'arguments': step.arguments,
                              'rationale': step.rationale,
                          }
                          for step in plan.steps
                      ],
                      'planner_context': plan_context,
                      'model': llm_plan_response.model,
                      'prompt_tokens': llm_plan_response.prompt_tokens,
                      'completion_tokens': llm_plan_response.completion_tokens,
                      'total_tokens': llm_plan_response.prompt_tokens + llm_plan_response.completion_tokens,
                      'cost_usd': plan_event_cost})
        await _emit(event_sink, 'plan', {'intent': plan.intent, 'steps_count': len(plan.steps),
                                          'narration_kind': plan.narration_kind})

        _stabilise_plan_for_supported_workflows(
            plan=plan,
            query=query,
            effective_query=effective_query,
            last_customer=last_customer,
            last_issue=last_issue,
            ledger=ledger,
        )

        if adversarial:
            ledger.event('adversarial', 'adversarial.block', {'flags': flags}, status='blocked')
            await _emit(event_sink, 'adversarial_block', {'flags': flags})
            return await _finalise_blocked(
                session, ledger, query, query_redacted, provider_name,
                conversation_ref, badge='Adversarial Input Blocked',
                answer=("I can't follow that instruction. Your message looked like an attempt to override my "
                        "policy or change role. User input is data, not instruction. Please rephrase as a "
                        "normal question."),
                intent='adversarial_block', started_ms=started_ms,
                llm_response=llm_plan_response,
            )

        confirm_pending = plan.intent == 'confirm_pending_action'
        if confirm_pending:
            # A bare confirmation is part of the deterministic write gate.
            # Always route it to _confirm_pending: that function gives a clear
            # "no pending action" or "permission denied" answer instead of
            # letting narration invent what happened.
            looks_like_confirm = _looks_like_confirmation(query)
            if looks_like_confirm:
                return await _confirm_pending(
                    session=session, ledger=ledger, mcp=mcp,
                    username=username, role=role, conversation_ref=conversation_ref,
                    query=query, query_redacted=query_redacted,
                    provider_name=provider_name, llm_plan_response=llm_plan_response,
                    started_ms=started_ms, event_sink=event_sink,
                )
            # Misrouted confirm: demote to a normal plan and continue with
            # whatever steps the LLM produced (or none — narration still runs).
            ledger.event('agent_plan', 'confirm_intent.demoted',
                         {'pending_present': bool(pending),
                          'message_chars': len(query),
                          'reason': 'no pending or message is not a bare affirmation'})
            plan.intent = 'demoted_confirm'
            plan.requires_clarification = False

        facts: dict[str, Any] = {'plan': plan.model_dump()}
        tools_called: list[str] = []
        skills_invoked: list[str] = []
        tool_latency_total = 0
        cumulative_evidence: list[str] = []

        async def run_tool(name: str, arguments: dict[str, Any]) -> None:
            nonlocal tool_latency_total
            await _emit(event_sink, 'tool_start', {'tool': name, 'args': arguments})
            with tracer.start_as_current_span(f'mcp.tool.{name}'):
                start_t = time.perf_counter()
                try:
                    output = await mcp.call_tool(name, arguments)
                    latency = int((time.perf_counter() - start_t) * 1000)
                    tool_latency_total += latency
                    ledger.tool(name, arguments, _summarise(output), 'ok', latency)
                    ledger.event('tool_call', f'tool.{name}.complete',
                                 {'tool': name,
                                  'request': arguments,
                                  'result_summary': _summarise(output),
                                  'result_keys': list(output.keys())[:8],
                                  'evidence_added': _evidence_from_tool_output(name, output)},
                                 latency_ms=latency)
                    await _emit(event_sink, 'tool_complete',
                                {'tool': name, 'summary': _summarise(output), 'latency_ms': latency})
                    await _ingest_tool_output(name, arguments, output, facts, cumulative_evidence,
                                               username, conversation_ref)
                except MCPClientError as exc:
                    latency = int((time.perf_counter() - start_t) * 1000)
                    ledger.tool(name, arguments, {'error': str(exc)}, 'error', latency, str(exc))
                    ledger.event('error', f'tool.{name}.error', {'error': str(exc)},
                                 status='error', latency_ms=latency)
                    await _emit(event_sink, 'tool_error', {'tool': name, 'error': str(exc)})
            tools_called.append(name)

        for step in plan.steps:
            ok_s, why_s = validate_step(step.step_type, step.name)
            ok_a, why_a = validate_step_arguments(step.name, step.arguments)
            if not ok_s or not ok_a:
                reason = why_s if not ok_s else why_a
                ledger.event('error', f'plan.step.rejected.{step.name}',
                             {'reason': reason}, status='error')
                # Surface in the streaming UI as a soft "skipped" entry so the
                # user can see the LLM tried something we couldn't run, instead
                # of a step silently vanishing from the plan card.
                await _emit(event_sink, 'tool_skipped',
                            {'tool': step.name, 'reason': reason})
                continue

            if step.step_type == 'tool':
                if step.name in WRITE_TOOLS:
                    ledger.event('error', f'plan.write_tool_in_plan.{step.name}',
                                 {'reason': 'write tools must go through propose-confirm'}, status='error')
                    continue
                await run_tool(step.name, step.arguments)

            elif step.step_type == 'skill':
                await _emit(event_sink, 'skill_start', {'skill': step.name})
                with tracer.start_as_current_span(f'skill.{step.name}'):
                    start_t = time.perf_counter()
                    skill_output = _invoke_skill(step.name, step.arguments, facts, role)
                    latency = int((time.perf_counter() - start_t) * 1000)
                if step.name == 'customer_escalation_summary':
                    fact = _ensure_customer_fact(facts, _customer_fact_key(step.arguments))
                    fact['skill_output'] = skill_output
                ledger.event('skill_invocation', f'skill.{step.name}.complete',
                             {'risk_level': skill_output.get('risk_level'),
                              'request': step.arguments,
                              'inputs_used': {
                                  'customer_profile': bool(
                                      _ensure_customer_fact(facts, _customer_fact_key(step.arguments)).get('customer_profile')
                                      or facts.get('customer_profile')
                                  ),
                                  'open_issues': len(
                                      _ensure_customer_fact(facts, _customer_fact_key(step.arguments)).get('open_issues')
                                      or facts.get('open_issues') or []
                                  ),
                                  'issue_updates': len(facts.get('all_updates') or []),
                                  'issue_history': bool(facts.get('issue_history')),
                              },
                              'output_summary': _summarise(skill_output),
                              'recommended_next_action': skill_output.get('recommended_next_action')},
                             latency_ms=latency)
                await _emit(event_sink, 'skill_complete',
                            {'skill': step.name, 'risk_level': skill_output.get('risk_level'),
                             'latency_ms': latency})
                facts['skill_output'] = skill_output
                facts['skill_name'] = step.name
                skills_invoked.append(step.name)
                cumulative_evidence.extend(skill_output.get('evidence', []))

        first_issue_ref = None
        for issue in facts.get('open_issues') or []:
            if issue.get('issue_ref'):
                first_issue_ref = issue['issue_ref']
                break
        needs_latest_status = any(
            phrase in effective_query.lower()
            for phrase in ('latest status', 'recommended next step', 'escalation summary', 'management attention')
        )
        if first_issue_ref and needs_latest_status and 'summarise_issue_history' not in tools_called:
            await run_tool('summarise_issue_history', {'issue_ref': first_issue_ref})
        if (
            first_issue_ref
            and ('recommended next step' in effective_query.lower() or 'what should we do next' in effective_query.lower())
            and 'recommend_next_action' not in tools_called
        ):
            await run_tool('recommend_next_action', {'issue_ref': first_issue_ref})

        requested_issue_ref = _first_issue_ref(effective_query, last_issue)
        if _explicit_escalation_intent(effective_query) and requested_issue_ref:
            base_rec = facts.get('recommendation') or {}
            evidence = list(base_rec.get('evidence') or cumulative_evidence or [f'issue:{requested_issue_ref}'])
            facts['recommendation'] = {
                'issue_ref': requested_issue_ref,
                'action_type': 'ESCALATE_ISSUE',
                'priority': base_rec.get('priority') or 'High',
                'title': f'Escalate issue {requested_issue_ref}',
                'description': f'Escalate {requested_issue_ref} for support follow-up.',
                'rationale': base_rec.get('rationale') or 'User explicitly requested escalation.',
                'evidence': evidence,
            }
            cumulative_evidence.extend(evidence)

        proposed_dto: ProposedActionDTO | None = None
        write_intent = _explicit_write_intent(effective_query)
        if (
            write_intent
            and not plan.write_requested
            and not plan.requires_clarification
            and (facts.get('recommendation') or (facts.get('skill_output') or {}).get('recommended_next_action'))
        ):
            ledger.event(
                'agent_plan',
                'write_request.inferred_from_context',
                {'reason': 'explicit write intent in effective query with recommended action'},
            )
            plan.write_requested = True
        if plan.write_requested and not plan.requires_clarification and not write_intent:
            ledger.event(
                'agent_plan',
                'write_request.suppressed',
                {'reason': 'user query did not explicitly ask to create/propose an action'},
            )
            plan.write_requested = False

        if plan.write_requested and not plan.requires_clarification:
            proposed_dto = await _maybe_propose(
                ledger=ledger, role=role, username=username,
                conversation_ref=conversation_ref, trace_ref=trace_ref,
                facts=facts, event_sink=event_sink,
            )

        # Final clearance: if the tool loop ended with a unique customer
        # resolved (regardless of order), drop any earlier ambiguity hint.
        # Avoids the "you confirmed Acme Manufacturing Group but the LLM
        # still re-asks because get_open_issues was called with bare 'Acme'"
        # failure mode.
        cp = facts.get('customer_profile') or {}
        if cp.get('name') and not cp.get('multiple_matches'):
            facts.pop('ambiguous_customer', None)

        # MCP ambiguity gate: if any tool returned multiple_matches, the agent
        # must not narrate as if it had a single answer.
        #
        # Architectural note (per chosen-model recommendation principle):
        # we DO NOT hardcode the clarification text here. Instead we:
        #   1. mark the plan as requires_clarification (badge → Clarification Required)
        #   2. leave clarification_question empty so the existing branch in
        #      the answer-selection block falls through to narration
        #   3. trust the chosen model to compose the
        #      clarification in its own voice, with facts.ambiguous_customer
        #      visible in the narration context and the NARRATION_PREAMBLE
        #      explicitly instructing the model on what to do.
        #
        # The deterministic_fallback below only fires if narration itself is
        # unreachable — same safety net as everywhere else.
        ambig = facts.get('ambiguous_customer')
        clarification_options: list[ClarificationOptionDTO] = []
        if ambig and ambig.get('matches'):
            matches = ambig['matches']
            clarification_options = _customer_clarification_options(matches)
            ledger.event('agent_plan', 'ambiguous_customer.detected',
                         {'queried': ambig.get('queried'),
                          'candidates': [m['name'] for m in matches]})
            plan.requires_clarification = True
            # Keep the narration facts in sync with the mutated plan.
            facts['plan'] = plan.model_dump()
            # Pre-built fallback used only if the chosen model's narration
            # fails or comes back empty. The model gets first chance.
            facts['ambiguous_customer_fallback_text'] = (
                f'### Multiple customers match "{ambig.get("queried")}"\n\n'
                f'I found {len(matches)} customers in scope. Which one did you mean?\n\n'
                + '\n'.join(
                    f'- **{m["name"]}** · {m.get("tier", "")} · {m.get("region", "")}'
                    for m in matches
                )
            )

        narration_provider = get_provider(provider_name)
        # Narration also sees the history so short follow-ups produce coherent
        # answers ("yes" → "OK, here's the briefing on Northwind").
        comparison_customers = _comparison_customers(facts)
        if len(comparison_customers) > 1:
            facts['comparison_customers'] = comparison_customers
        facts['conversation_history'] = history_text or None
        try:
            narration_facts = sanitize_facts_for_llm(facts, privacy) if privacy.external else facts
            narration_response_raw = await narration_provider.narrate(
                NARRATION_PREAMBLE,
                llm_enriched_query,
                narration_facts,
            )
            narration_response = LLMResponse(
                text=restore_text_from_llm(narration_response_raw.text, privacy)
                if privacy.external else narration_response_raw.text,
                prompt_tokens=narration_response_raw.prompt_tokens,
                completion_tokens=narration_response_raw.completion_tokens,
                latency_ms=narration_response_raw.latency_ms,
                model=narration_response_raw.model,
                raw=narration_response_raw.raw,
            )
            if privacy.external:
                ledger.event(
                    'privacy',
                    'outbound_llm.narration_payload',
                    privacy_diff(
                        readable_query=trace_enriched_query,
                        outbound_query=llm_enriched_query,
                        readable_facts=facts,
                        outbound_facts=narration_facts,
                        inbound_text=narration_response_raw.text,
                        restored_text=narration_response.text,
                        privacy=privacy,
                        applied=llm_sanitized,
                    ),
                    latency_ms=narration_response_raw.latency_ms,
                )
        except Exception as exc:
            ledger.event('error', 'llm.narrate.unavailable', {'error': str(exc)}, status='error')
            # Soft-fall back to a templated answer so the user still sees something useful.
            narration_response = LLMResponse(
                text=(plan.clarification_question
                      or 'The LLM did not return an answer. The trace records the tool results.'),
                prompt_tokens=0, completion_tokens=0, latency_ms=0,
                model=llm_plan_response.model,
            )
        narration_event_model_key = _model_key_for_cost(narration_response.model or '', provider_name)
        narration_event_cost = compute_cost(
            narration_event_model_key,
            narration_response.prompt_tokens,
            narration_response.completion_tokens,
        )
        if facts.get('proposal_denied'):
            denied = facts['proposal_denied']
            cumulative_evidence.extend([
                f'user:{username}',
                f'action_policy:{denied.get("action_type")}',
            ])
        evidence_list = sorted(set(cumulative_evidence))[:20]
        ledger.event('final_response', 'narration.complete',
                     {'model': narration_response.model, 'len': len(narration_response.text),
                      'narration_input': {
                          'query': trace_enriched_query[:3000],
                          'outbound_query': llm_enriched_query[:3000] if privacy.external else None,
                          'fact_keys': sorted(facts.keys()),
                          'evidence_count': len(evidence_list),
                      },
                      'answer_preview': narration_response.text[:1200],
                      'prompt_tokens': narration_response.prompt_tokens,
                      'completion_tokens': narration_response.completion_tokens,
                      'total_tokens': narration_response.prompt_tokens + narration_response.completion_tokens,
                      'cost_usd': narration_event_cost,
                      'evidence': evidence_list},
                     latency_ms=narration_response.latency_ms)

        if plan.requires_clarification and plan.clarification_question:
            # Planner already supplied a deterministic clarification question.
            answer = plan.clarification_question
            badge = 'Clarification Required'
        elif plan.requires_clarification:
            # The chosen model is responsible for the
            # text. Only fall back to deterministic text if narration was
            # empty or unavailable — preserves the chosen-model recommendation
            # principle.
            narrated = (narration_response.text or '').strip()
            answer = narrated or facts.get('ambiguous_customer_fallback_text') \
                     or 'Could you clarify which customer or issue you mean?'
            badge = 'Clarification Required'
        elif facts.get('proposal_denied'):
            denied = facts['proposal_denied']
            answer = _proposal_denied_answer(
                str(denied.get('role') or role),
                str(denied.get('action_type') or ''),
                str(denied.get('reason') or 'role is not allowed to create this action'),
            )
            badge = 'Permission Denied'
        elif proposed_dto is not None:
            answer = narration_response.text
            badge = 'Confirmation Required'
        elif plan.intent == 'adversarial':
            answer = narration_response.text
            badge = 'Adversarial Input Blocked'
        else:
            answer = narration_response.text
            has_evidence = bool(cumulative_evidence) or bool(facts.get('skill_output'))
            if not has_evidence and not plan.steps:
                badge = 'Conversational'
            else:
                badge = badge_for(has_evidence=has_evidence)

        comparison_customers = facts.get('comparison_customers') or []
        if (
            not plan.requires_clarification
            and badge != 'Permission Denied'
            and len(comparison_customers) > 1
            and _needs_customer_comparison_fallback(answer, comparison_customers)
        ):
            answer = _render_customer_comparison_answer(facts, role)
            ledger.event(
                'final_response',
                'narration.quality_fallback',
                {'reason': 'missing_customer_comparison_details', 'len': len(answer)},
            )
        elif (
            not plan.requires_clarification
            and badge != 'Permission Denied'
            and not comparison_customers
            and _needs_customer_status_fallback(answer, facts)
        ):
            answer = _render_customer_status_answer(facts, role)
            ledger.event(
                'final_response',
                'narration.quality_fallback',
                {'reason': 'missing_customer_status_details', 'len': len(answer)},
            )

        plan_model = llm_plan_response.model or ''
        narration_model = narration_response.model or plan_model
        plan_model_key = _model_key_for_cost(plan_model, provider_name)
        narration_model_key = _model_key_for_cost(narration_model, provider_name)
        prompt_tokens = llm_plan_response.prompt_tokens + narration_response.prompt_tokens
        completion_tokens = llm_plan_response.completion_tokens + narration_response.completion_tokens
        cost_usd = (
            compute_cost(plan_model_key, llm_plan_response.prompt_tokens, llm_plan_response.completion_tokens)
            + compute_cost(narration_model_key, narration_response.prompt_tokens, narration_response.completion_tokens)
        )
        llm_latency = llm_plan_response.latency_ms + narration_response.latency_ms
        skill_output = facts.get('skill_output') or {}
        auto_route = None
        auto_route_confidence = None
        auto_route_source = None
        if route_decision is not None:
            auto_route = getattr(route_decision, 'route', None)
            auto_route_confidence = getattr(route_decision, 'confidence', None)
            auto_route_source = getattr(route_decision, 'source', None)
        used_external_llm = _external_llm_used(plan_model, narration_model, auto_route_source)
        response_risk_level = skill_output.get('risk_level')
        response_missing_information = skill_output.get('missing_information', [])
        if comparison_customers:
            highest_risk_customer = max(
                comparison_customers,
                key=lambda row: (
                    _risk_rank(row.get('risk_level')),
                    max((_issue_rank(issue) for issue in row.get('open_issues') or []), default=0),
                ),
            )
            response_risk_level = highest_risk_customer.get('risk_level')
            response_missing_information = list(dict.fromkeys(
                str(item)
                for row in comparison_customers
                for item in (row.get('missing_information') or [])
            ))

        # Order matters: the conversation row MUST exist before insert_trace runs,
        # otherwise the FK lookup in repositories.insert_trace returns NULL and the
        # trace is orphaned (invisible in /chat?conversation_ref=... history).
        await repo.conversation_upsert(session, conversation_ref, username, query[:200])
        await ledger_mod.persist(
            ledger=ledger,
            session=session,
            conversation_ref=conversation_ref,
            user_query=query,
            user_query_redacted=query_redacted,
            detected_intent=plan.intent,
            final_answer=answer,
            final_status=badge,
            llm_provider=provider_name,
            llm_model=narration_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=cost_usd,
            llm_latency_ms=llm_latency,
            tool_latency_ms=tool_latency_total,
            otel_trace_id=current_trace_id_hex(),
        )
        await conversation_memory.append_context(
            username, conversation_ref,
            {'role': 'user', 'text': query[:500]},
        )
        await conversation_memory.append_context(
            username, conversation_ref,
            {'role': 'assistant', 'text': answer[:500], 'trace_ref': trace_ref},
        )

        latency_ms = int(time.time() * 1000) - started_ms

        await _emit(event_sink, 'final_response', {
            'trace_ref': trace_ref, 'badge': badge, 'answer': answer,
            'cost_usd': cost_usd, 'total_tokens': prompt_tokens + completion_tokens,
            'latency_ms': latency_ms,
            'plan_model': plan_model,
            'narration_model': narration_model,
            'route': auto_route,
            'route_confidence': auto_route_confidence,
            'route_source': auto_route_source,
            'used_external_llm': used_external_llm,
            'clarification_options': [option.model_dump(mode='json') for option in clarification_options],
        })

        return ChatResponse(
            trace_ref=trace_ref,
            intent=plan.intent,
            answer=answer,
            badge=badge,
            evidence=evidence_list,
            proposed_action=proposed_dto,
            tools_called=tools_called,
            skills_invoked=skills_invoked,
            risk_level=response_risk_level,
            missing_information=response_missing_information,
            cost_usd=cost_usd,
            total_tokens=prompt_tokens + completion_tokens,
            latency_ms=latency_ms,
            provider=provider_name,
            model=narration_model,
            plan_model=plan_model,
            narration_model=narration_model,
            route=auto_route,
            route_confidence=auto_route_confidence,
            route_source=auto_route_source,
            used_external_llm=used_external_llm,
            clarification_options=clarification_options,
            query_redacted=query_redacted,
        )


async def _ingest_tool_output(
    tool_name: str,
    arguments: dict[str, Any],
    output: dict[str, Any],
    facts: dict[str, Any],
    cumulative_evidence: list[str],
    username: str,
    conversation_ref: str,
) -> None:
    """Routes a tool's result into the facts dict + Redis short-term memory.

    The Redis writes (set_last_customer / set_last_issue) are what make
    follow-up references like "that customer" or "that issue" resolve on the
    next turn.
    """
    # Helper: did we already resolve a unique customer earlier in this turn?
    have_resolved_customer = (
        bool(facts.get('customer_profile'))
        and not facts['customer_profile'].get('multiple_matches')
        and facts['customer_profile'].get('name')
    )

    if tool_name == 'get_customer_profile':
        requested_key = _customer_fact_key(arguments, output)
        fact = _ensure_customer_fact(facts, requested_key)
        if output.get('multiple_matches'):
            # Only flag ambiguity if no earlier call already resolved a customer.
            if not have_resolved_customer:
                facts['customer_profile'] = output
                facts['ambiguous_customer'] = {
                    'queried': output.get('queried'),
                    'matches': output.get('matches', []),
                }
            fact['customer_profile'] = output
        elif output.get('name'):
            facts['customer_profile'] = output
            fact = _ensure_customer_fact(facts, output.get('name') or requested_key)
            fact['customer_profile'] = output
            # A successful unique match clears any earlier ambiguity hint —
            # the agent has now confirmed which customer is in scope.
            facts.pop('ambiguous_customer', None)
            cumulative_evidence.append(f'customer:{output.get("customer_id", output.get("name"))}')
            await conversation_memory.set_last_customer(username, conversation_ref, output)
        else:
            # not_found path
            facts['customer_profile'] = output
            fact['customer_profile'] = output
    elif tool_name == 'get_open_issues':
        requested_key = _customer_fact_key(arguments, output)
        fact = _ensure_customer_fact(facts, requested_key)
        if output.get('multiple_matches'):
            if not have_resolved_customer:
                facts['ambiguous_customer'] = {
                    'queried': output.get('queried'),
                    'matches': output.get('matches', []),
                }
            facts['open_issues'] = []
            fact['open_issues'] = []
        else:
            facts['open_issues'] = output.get('issues', [])
            fact['open_issues'] = output.get('issues', [])
            if output.get('customer_id'):
                fact['customer_id'] = output.get('customer_id')
            for issue in output.get('issues', []):
                cumulative_evidence.append(f'issue:{issue.get("issue_ref")}')
            issues = output.get('issues') or []
            if issues and issues[0].get('issue_ref'):
                await conversation_memory.set_last_issue(username, conversation_ref, issues[0]['issue_ref'])
    elif tool_name == 'summarise_issue_history':
        facts['issue_history'] = output
        cumulative_evidence.extend(output.get('evidence', []))
        facts.setdefault('all_updates', []).extend(output.get('updates', []))
        if output.get('issue_ref'):
            await conversation_memory.set_last_issue(username, conversation_ref, output['issue_ref'])
    elif tool_name == 'recommend_next_action':
        facts['recommendation'] = output
        cumulative_evidence.extend(output.get('evidence', []))
    elif tool_name == 'search_customers':
        facts['matches'] = output.get('matches', [])
        matches = output.get('matches') or []
        if len(matches) == 1 and matches[0].get('name'):
            # Unambiguous match — remember it.
            await conversation_memory.set_last_customer(username, conversation_ref, matches[0])


def _invoke_skill(
    skill_name: str,
    arguments: dict[str, Any],
    facts: dict[str, Any],
    role: str,
) -> dict[str, Any]:
    skill = SKILLS[skill_name]
    if skill_name == 'customer_escalation_summary':
        fact = _ensure_customer_fact(facts, _customer_fact_key(arguments))
        return skill(
            customer=fact.get('customer_profile') or facts.get('customer_profile') or {'name': arguments.get('customer_name', '')},
            issues=fact.get('open_issues', facts.get('open_issues', [])),
            updates=facts.get('all_updates', []),
            actor_role=role,
        )
    if skill_name == 'closure_readiness_check':
        history = facts.get('issue_history') or {}
        return skill(
            issue_ref=arguments.get('issue_ref') or history.get('issue_ref', ''),
            issue=history if history else None,
            updates=history.get('updates', []) if history else [],
            open_actions=[],
        )
    return {}


def _needs_customer_status_fallback(answer: str | None, facts: dict[str, Any]) -> bool:
    """Detect under-answered customer status narrations.

    Local models can occasionally produce a grammatical but incomplete answer
    even when the tool facts are rich. When issues or risk output are present,
    the user-facing answer must at least name the issue refs and include a
    recommendation/risk signal.
    """
    issues = facts.get('open_issues') or []
    skill_output = facts.get('skill_output') or {}
    if not issues and not skill_output:
        return False

    text = (answer or '').strip()
    if len(text) < 120:
        return True

    lower = text.lower()
    issue_refs = [str(i.get('issue_ref')) for i in issues if i.get('issue_ref')]
    if issue_refs and any(ref.lower() not in lower for ref in issue_refs):
        return True

    if issues and not any(word in lower for word in ('status', 'sla', 'severity', 'risk')):
        return True

    if skill_output.get('recommended_next_action') and not any(
        word in lower for word in ('recommend', 'next step', 'follow up', 'prepare', 'escalate', 'assign')
    ):
        return True

    return False


def _needs_customer_comparison_fallback(
    answer: str | None,
    comparison_customers: list[dict[str, Any]],
) -> bool:
    text = (answer or '').strip()
    if len(text) < 160:
        return True

    lower = text.lower()
    for row in comparison_customers:
        name = str(row.get('customer_name') or '').strip()
        if name and name.lower() not in lower:
            return True
        for issue in row.get('open_issues') or []:
            ref = str(issue.get('issue_ref') or '').strip()
            if ref and ref.lower() not in lower:
                return True

    return not any(word in lower for word in ('more urgent', 'urgency', 'priority', 'risk'))


def _risk_rank(risk: Any) -> int:
    return {
        'critical': 4,
        'high': 3,
        'medium': 2,
        'low': 1,
    }.get(str(risk or '').strip().lower(), 0)


def _issue_rank(issue: dict[str, Any]) -> int:
    severity = str(issue.get('severity') or '').strip().upper()
    severity_rank = {'P1': 4, 'P2': 3, 'P3': 2, 'P4': 1}.get(severity, 0)
    sla = str(issue.get('sla_status') or issue.get('sla') or '').strip().lower()
    sla_rank = 2 if 'breach' in sla else 1 if 'risk' in sla else 0
    return severity_rank * 10 + sla_rank


def _render_customer_comparison_answer(facts: dict[str, Any], role: str) -> str:
    rows = list(facts.get('comparison_customers') or [])
    rows.sort(
        key=lambda row: (
            _risk_rank(row.get('risk_level')),
            max((_issue_rank(issue) for issue in row.get('open_issues') or []), default=0),
        ),
        reverse=True,
    )

    if not rows:
        return 'I could not find enough customer facts to compare urgency.'

    top = rows[0]
    top_name = top.get('customer_name') or 'The first customer'
    lines: list[str] = [
        f'### Urgency Comparison',
        '',
        f'**{top_name}** more urgently needs action.',
        '',
    ]

    for row in rows:
        name = row.get('customer_name') or 'Customer'
        profile = row.get('profile') or {}
        issues = row.get('open_issues') or []
        risk = row.get('risk_level') or 'Unknown'
        recommendation = row.get('recommended_next_action') or {}
        tier_region = ' · '.join(str(bit) for bit in (profile.get('tier'), profile.get('region')) if bit)

        lines.append(f'### {name}')
        if tier_region:
            lines.append(f'- {tier_region}')
        lines.append(f'- Risk level: **{risk}**')

        if issues:
            lines.append(f'- Open issues: {len(issues)}')
            for issue in issues:
                ref = issue.get('issue_ref') or 'Issue'
                title = issue.get('title') or 'Untitled issue'
                details = [
                    issue.get('severity'),
                    issue.get('status'),
                    issue.get('sla_status') or issue.get('sla'),
                ]
                detail_text = ', '.join(str(detail) for detail in details if detail)
                suffix = f' ({detail_text})' if detail_text else ''
                lines.append(f'  - **{ref}**: {title}{suffix}')
        else:
            lines.append('- Open issues: none found in the retrieved facts')

        if recommendation:
            action_type = str(recommendation.get('action_type') or 'FOLLOW_UP')
            priority = recommendation.get('priority') or 'Medium'
            rationale = recommendation.get('rationale')
            action_label = action_type.replace('_', ' ').title()
            if action_type == 'PREPARE_RECOVERY_PLAN' and role == 'sales_user':
                lines.append(f'- Recommended next step: ask Support to prepare a recovery plan ({priority} priority).')
            else:
                lines.append(f'- Recommended next step: {action_label} ({priority} priority).')
            if rationale:
                lines.append(f'- Rationale: {rationale}')

        lines.append('')

    if len(rows) > 1:
        runner_up = rows[1].get('customer_name') or 'the next customer'
        top_risk = top.get('risk_level') or 'higher'
        runner_risk = rows[1].get('risk_level') or 'lower'
        lines.extend([
            '### Verdict',
            '',
            f'- **{top_name}** is more urgent than **{runner_up}** because its risk is **{top_risk}** versus **{runner_risk}**.',
        ])

    missing = []
    for row in rows:
        missing.extend(str(item) for item in row.get('missing_information') or [])
    if missing:
        lines.extend(['', '### Confirm On The Call', ''])
        for item in dict.fromkeys(missing):
            lines.append(f'- {item}')

    return '\n'.join(lines).strip()


def _render_customer_status_answer(facts: dict[str, Any], role: str) -> str:
    customer = facts.get('customer_profile') or {}
    issues = facts.get('open_issues') or []
    skill_output = facts.get('skill_output') or {}
    recommendation = facts.get('recommendation') or skill_output.get('recommended_next_action') or {}

    name = customer.get('name') or 'Customer'
    tier = customer.get('tier')
    region = customer.get('region')
    risk = skill_output.get('risk_level')

    lines: list[str] = [f'### Status Update for {name}', '']
    profile_bits = [bit for bit in (tier, region) if bit]
    if profile_bits:
        lines.append(f'- {" · ".join(profile_bits)}')
    if risk:
        lines.append(f'- Risk level: **{risk}**')

    lines.extend(['', '### Open Issues', ''])
    if issues:
        for issue in issues:
            ref = issue.get('issue_ref') or 'Issue'
            title = issue.get('title') or 'Untitled issue'
            details = [
                issue.get('severity'),
                issue.get('status'),
                issue.get('sla_status') or issue.get('sla'),
            ]
            owner = issue.get('owner')
            detail_text = ', '.join(str(d) for d in details if d)
            if owner:
                detail_text = f'{detail_text}, Owner: {owner}' if detail_text else f'Owner: {owner}'
            suffix = f' ({detail_text})' if detail_text else ''
            lines.append(f'- **{ref}**: {title}{suffix}')
    else:
        lines.append('- No open issues were found in the retrieved facts.')

    lines.extend(['', '### Latest Status', ''])
    executive_summary = skill_output.get('executive_summary')
    if executive_summary:
        lines.append(f'- {executive_summary}')
    risk_factors = skill_output.get('risk_factors') or []
    for factor in risk_factors:
        lines.append(f'- {factor}')

    lines.extend(['', '### Recommended Next Step', ''])
    if recommendation:
        action_type = str(recommendation.get('action_type') or 'FOLLOW_UP')
        priority = recommendation.get('priority') or 'Medium'
        action_label = action_type.replace('_', ' ').title()
        rationale = recommendation.get('rationale')
        if action_type == 'PREPARE_RECOVERY_PLAN' and role == 'sales_user':
            lines.append(
                f'- Ask Support to prepare a recovery plan for the highest-priority open issue '
                f'({priority} priority).'
            )
        else:
            lines.append(f'- Recommend: {action_label} ({priority} priority).')
        if rationale:
            lines.append(f'- Rationale: {rationale}')
    else:
        lines.append('- No specific recommendation was returned in the retrieved facts.')

    missing = skill_output.get('missing_information') or []
    if missing:
        lines.extend(['', '### Confirm On The Call', ''])
        for item in missing:
            lines.append(f'- {item}')

    return '\n'.join(lines).strip()


async def _maybe_propose(
    *,
    ledger: ledger_mod.Ledger,
    role: str,
    username: str,
    conversation_ref: str,
    trace_ref: str,
    facts: dict[str, Any],
    event_sink: EventSink,
) -> ProposedActionDTO | None:
    recommendation = facts.get('recommendation') or (facts.get('skill_output') or {}).get('recommended_next_action')
    if not recommendation:
        ledger.event('error', 'propose.no_recommendation', {}, status='error')
        return None
    action_type = recommendation.get('action_type', '')
    allowed, reason = can_propose(role, action_type)
    ledger.rbac(role=role, operation='create_action', resource=action_type, allowed=allowed, reason=reason)
    ledger.event('rbac_decision', 'rbac.create_action',
                 {'role': role, 'action_type': action_type, 'allowed': allowed, 'reason': reason},
                 status='ok' if allowed else 'denied')
    if not allowed:
        facts['proposal_denied'] = {
            'action_type': action_type,
            'reason': reason,
            'role': role,
        }
        await _emit(event_sink, 'rbac_denied', {'reason': reason, 'action_type': action_type})
        return None
    issue_history = facts.get('issue_history') or {}
    open_issues = facts.get('open_issues') or []
    issue_ref = (
        recommendation.get('issue_ref')
        or issue_history.get('issue_ref')
        or (open_issues[0].get('issue_ref') if open_issues else '')
        or 'ISS-102'
    )
    customer_profile = facts.get('customer_profile') or {}
    proposed = build_proposed_action(
        trace_ref=trace_ref,
        customer_id=customer_profile.get('customer_id'),
        customer_name=customer_profile.get('name'),
        issue_ref=issue_ref,
        recommendation=recommendation,
    )
    await stage_pending_action(conversation_ref, proposed)
    # Persist the full proposal payload (minus the token, which is short-lived)
    # so we can rebuild the Confirm card from PG when Redis has expired.
    ledger.event('action_proposed', 'action.proposed',
                 {k: v for k, v in proposed.items() if k != 'confirmation_token'})
    await _emit(event_sink, 'proposed_action',
                {'action_type': action_type, 'issue_ref': issue_ref, 'priority': proposed.get('priority'),
                 'title': proposed.get('title'), 'confirmation_token': proposed.get('confirmation_token')})
    return ProposedActionDTO(
        action_type=proposed['action_type'],
        title=proposed.get('title', ''),
        description=proposed.get('description', ''),
        priority=proposed.get('priority', 'Medium'),
        issue_ref=issue_ref,
        customer_id=proposed.get('customer_id'),
        customer_name=proposed.get('customer_name'),
        rationale=proposed.get('rationale', ''),
        evidence=list(proposed.get('evidence', [])),
        due_at=proposed.get('due_at'),
        confirmation_token=proposed['confirmation_token'],
        idempotency_key=proposed['idempotency_key'],
        trace_ref=trace_ref,
        expires_at=proposed['expires_at'],
    )


async def _confirm_pending(
    *,
    session: AsyncSession,
    ledger: ledger_mod.Ledger,
    mcp: MCPClient,
    username: str,
    role: str,
    conversation_ref: str,
    query: str,
    query_redacted: str,
    provider_name: str,
    llm_plan_response: Any,
    started_ms: int,
    event_sink: EventSink,
) -> ChatResponse:
    pending = await get_pending_action(conversation_ref)
    if not pending:
        return await _finalise_blocked(
            session, ledger, query, query_redacted, provider_name, conversation_ref,
            badge='Clarification Required',
            answer=(
                'There is no pending proposed action to confirm in this conversation. '
                'Nothing was created. Ask me to propose the action again; if your role '
                'is allowed to create it, I will show a confirmation card.'
            ),
            intent='no_pending_action', started_ms=started_ms,
            llm_response=llm_plan_response,
        )
    ok_tok, why_tok, _ = verify_confirmation_token(pending['confirmation_token'])
    ledger.event('action_validation', 'token.verify',
                 {'ok': ok_tok, 'reason': why_tok},
                 status='ok' if ok_tok else 'error')
    allowed, reason = can_propose(role, pending['action_type'])
    ledger.rbac(role=role, operation='create_action', resource=pending['action_type'],
                allowed=allowed, reason=reason)
    if not (ok_tok and allowed):
        return await _finalise_blocked(
            session, ledger, query, query_redacted, provider_name, conversation_ref,
            badge='Permission Denied',
            answer=f'Cannot confirm action: {why_tok if not ok_tok else reason}',
            intent='confirm_denied', started_ms=started_ms,
            llm_response=llm_plan_response,
        )

    payload = {
        'actor': {'username': username, 'role': role},
        'issue_ref': pending['issue_ref'],
        'action_type': pending['action_type'],
        'title': pending.get('title', ''),
        'description': pending.get('description', ''),
        'priority': pending.get('priority', 'Medium'),
        'due_at': pending.get('due_at'),
        'evidence': pending.get('evidence', []),
        'idempotency_key': pending['idempotency_key'],
        'confirmation_token': pending['confirmation_token'],
    }
    try:
        result = await mcp.call_tool('create_next_action', payload)
        ledger.tool('create_next_action', payload, ledger_mod.summarise_output(result), 'ok', 0)
        ledger.event('action_confirmed', 'action.confirmed', result)
        await _emit(event_sink, 'action_confirmed', result)
        if result.get('duplicate'):
            answer = f'Action already exists ({result["existing_action_ref"]}); no duplicate was created.'
            badge = 'Action Created'
        elif result.get('created'):
            answer = f'Action {result["action_ref"]} created for {pending["issue_ref"]} and assigned to {username}.'
            badge = 'Action Created'
        else:
            answer = f'Confirmation rejected: {result.get("reason")}'
            badge = 'Permission Denied'
    except MCPClientError as exc:
        ledger.event('error', 'create_next_action.error', {'error': str(exc)}, status='error')
        answer = f'Failed to create action: {exc}'
        badge = 'Insufficient Evidence'

    prompt_tokens = llm_plan_response.prompt_tokens
    completion_tokens = llm_plan_response.completion_tokens
    cost = compute_cost(provider_name, prompt_tokens, completion_tokens)
    ledger.event('final_response', 'action.confirmation_response',
                 {'len': len(answer),
                  'evidence': list(pending.get('evidence', [])),
                  'action_type': pending.get('action_type'),
                  'issue_ref': pending.get('issue_ref'),
                  'created': bool(result.get('created')) if 'result' in locals() else False,
                  'duplicate': bool(result.get('duplicate')) if 'result' in locals() else False})
    await repo.conversation_upsert(session, conversation_ref, username, query[:200])
    await ledger_mod.persist(
        ledger=ledger, session=session,
        conversation_ref=conversation_ref,
        user_query=query, user_query_redacted=query_redacted,
        detected_intent='confirm_pending_action', final_answer=answer, final_status=badge,
        llm_provider=provider_name, llm_model=llm_plan_response.model,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        estimated_cost_usd=cost, llm_latency_ms=llm_plan_response.latency_ms,
        tool_latency_ms=0, otel_trace_id=current_trace_id_hex(),
    )
    return ChatResponse(
        trace_ref=ledger.trace_ref, intent='confirm_pending_action', answer=answer, badge=badge,
        evidence=list(pending.get('evidence', [])),
        tools_called=['create_next_action'], skills_invoked=[],
        cost_usd=cost, total_tokens=prompt_tokens + completion_tokens,
        latency_ms=int(time.time() * 1000) - started_ms,
        provider=provider_name, model=llm_plan_response.model,
        query_redacted=query_redacted,
    )


async def _handle_preplan_ambiguity(
    *,
    session: AsyncSession,
    ledger: ledger_mod.Ledger,
    username: str,
    role: str,
    conversation_ref: str,
    query: str,
    query_redacted: str,
    provider_name: str,
    started_ms: int,
    event_sink: EventSink,
    history_text: str,
    ambiguity: dict[str, Any],
    privacy: OutboundPrivacyContext,
) -> ChatResponse:
    """Short-circuit path for queries the orchestrator knows are ambiguous
    before any LLM planning. We still call narrate so the answer is composed
    by the chosen model (architectural principle), but skip
    the plan / tool stages entirely — they would only let the LLM hide the
    ambiguity by pre-resolving it."""
    matches = ambiguity['matches']
    fallback_text = (
        f'### Multiple customers match "{ambiguity.get("queried")}"\n\n'
        f'I found {len(matches)} customers in scope. Which one did you mean?\n\n'
        + '\n'.join(
            f'- **{m["name"]}** · {m.get("tier", "")} · {m.get("region", "")}'
            for m in matches
        )
    )
    facts: dict[str, Any] = {
        'plan': {
            'intent': 'disambiguate_customer',
            'requires_clarification': True,
            'steps': [],
            'write_requested': False,
            'narration_kind': 'clarification',
        },
        'ambiguous_customer': ambiguity,
        'ambiguous_customer_fallback_text': fallback_text,
        'conversation_history': history_text or None,
    }

    narration_provider = get_provider(provider_name)
    try:
        llm_user_prompt = f'{history_text}\nCurrent message:\n{query}' if history_text else query
        llm_user_prompt = sanitize_text_for_llm(llm_user_prompt, privacy) if privacy.external else llm_user_prompt
        llm_facts = sanitize_facts_for_llm(facts, privacy) if privacy.external else facts
        narration_response_raw = await narration_provider.narrate(
            NARRATION_PREAMBLE,
            llm_user_prompt,
            llm_facts,
        )
        narration_response = LLMResponse(
            text=restore_text_from_llm(narration_response_raw.text, privacy)
            if privacy.external else narration_response_raw.text,
            prompt_tokens=narration_response_raw.prompt_tokens,
            completion_tokens=narration_response_raw.completion_tokens,
            latency_ms=narration_response_raw.latency_ms,
            model=narration_response_raw.model,
            raw=narration_response_raw.raw,
        )
        if privacy.external:
            ledger.event(
                'privacy',
                'outbound_llm.narration_payload',
                privacy_diff(
                    readable_query=query_redacted,
                    outbound_query=llm_user_prompt,
                    readable_facts=facts,
                    outbound_facts=llm_facts,
                    inbound_text=narration_response_raw.text,
                    restored_text=narration_response.text,
                    privacy=privacy,
                    applied=sanitize_text_with_report(llm_user_prompt, privacy) if privacy.external else None,
                ),
                latency_ms=narration_response_raw.latency_ms,
            )
        narrated = (narration_response.text or '').strip()
    except Exception as exc:
        ledger.event('error', 'llm.narrate.unavailable', {'error': str(exc)}, status='error')
        narrated = ''
        narration_response = None

    answer = narrated or fallback_text
    badge = 'Clarification Required'
    clarification_options = _customer_clarification_options(matches)

    prompt_tokens = narration_response.prompt_tokens if narration_response else 0
    completion_tokens = narration_response.completion_tokens if narration_response else 0
    model = narration_response.model if narration_response else ''
    llm_latency = narration_response.latency_ms if narration_response else 0
    cost = compute_cost(provider_name, prompt_tokens, completion_tokens)

    ledger.event('final_response', 'narration.complete',
                 {'model': model, 'len': len(answer),
                  'mode': 'preplan_clarification',
                  'prompt_tokens': prompt_tokens,
                  'completion_tokens': completion_tokens,
                  'total_tokens': prompt_tokens + completion_tokens,
                  'cost_usd': cost,
                  'evidence': []},
                 latency_ms=llm_latency)

    await repo.conversation_upsert(session, conversation_ref, username, query[:200])
    await ledger_mod.persist(
        ledger=ledger, session=session, conversation_ref=conversation_ref,
        user_query=query, user_query_redacted=query_redacted,
        detected_intent='disambiguate_customer',
        final_answer=answer, final_status=badge,
        llm_provider=provider_name, llm_model=model,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        estimated_cost_usd=cost, llm_latency_ms=llm_latency,
        tool_latency_ms=0, otel_trace_id=current_trace_id_hex(),
    )
    await conversation_memory.append_context(
        username, conversation_ref, {'role': 'user', 'text': query[:500]},
    )
    await conversation_memory.append_context(
        username, conversation_ref,
        {'role': 'assistant', 'text': answer[:500], 'trace_ref': ledger.trace_ref},
    )
    await _emit(event_sink, 'final_response', {
        'trace_ref': ledger.trace_ref, 'badge': badge, 'answer': answer,
        'cost_usd': cost, 'total_tokens': prompt_tokens + completion_tokens,
        'latency_ms': int(time.time() * 1000) - started_ms,
        'plan_model': '',
        'narration_model': model,
        'used_external_llm': _external_llm_used('', model or provider_name, None),
        'clarification_options': [option.model_dump(mode='json') for option in clarification_options],
    })

    return ChatResponse(
        trace_ref=ledger.trace_ref,
        intent='disambiguate_customer',
        answer=answer, badge=badge,
        evidence=[], proposed_action=None,
        tools_called=[], skills_invoked=[],
        risk_level=None, missing_information=[],
        cost_usd=cost, total_tokens=prompt_tokens + completion_tokens,
        latency_ms=int(time.time() * 1000) - started_ms,
        provider=provider_name, model=model,
        plan_model='',
        narration_model=model,
        used_external_llm=_external_llm_used('', model or provider_name, None),
        clarification_options=clarification_options,
        query_redacted=query_redacted,
    )


async def _handle_resolution_required(
    *,
    session: AsyncSession,
    ledger: ledger_mod.Ledger,
    username: str,
    conversation_ref: str,
    query: str,
    query_redacted: str,
    provider_name: str,
    started_ms: int,
    event_sink: EventSink,
    rules_decision: RouteDecision,
    model_decision: RouteDecision,
) -> ChatResponse:
    resolution = _resolution_payload(rules_decision, model_decision)
    answer = (
        'I found a classification conflict that needs your decision before I continue.\n\n'
        f'- Rules: **{rules_decision.route}**'
        + (f' — {rules_decision.reason}' if rules_decision.reason else '')
        + '\n'
        f'- Selected model: **{model_decision.route}**'
        + (f' — {model_decision.reason}' if model_decision.reason else '')
    )
    badge = 'Resolution Required'
    latency_ms = int(time.time() * 1000) - started_ms

    await _emit(event_sink, 'contradiction_required', resolution.model_dump(mode='json'))
    ledger.event('final_response', 'resolution.required',
                 {'len': len(answer), 'evidence': [], 'status': badge})

    await repo.conversation_upsert(session, conversation_ref, username, query[:200])
    await ledger_mod.persist(
        ledger=ledger,
        session=session,
        conversation_ref=conversation_ref,
        user_query=query,
        user_query_redacted=query_redacted,
        detected_intent='classification_conflict',
        final_answer=answer,
        final_status=badge,
        llm_provider=provider_name,
        llm_model='',
        prompt_tokens=0,
        completion_tokens=0,
        estimated_cost_usd=0.0,
        llm_latency_ms=0,
        tool_latency_ms=0,
        otel_trace_id=current_trace_id_hex(),
    )
    await conversation_memory.append_context(
        username, conversation_ref, {'role': 'user', 'text': query[:500]},
    )
    await conversation_memory.append_context(
        username, conversation_ref,
        {'role': 'assistant', 'text': answer[:500], 'trace_ref': ledger.trace_ref},
    )

    payload = {
        'trace_ref': ledger.trace_ref,
        'badge': badge,
        'answer': answer,
        'cost_usd': 0.0,
        'total_tokens': 0,
        'latency_ms': latency_ms,
        'route': rules_decision.route,
        'route_confidence': rules_decision.confidence,
        'route_source': 'conflict',
        'used_external_llm': _external_llm_used('', '', model_decision.source),
        'resolution_required': resolution.model_dump(mode='json'),
    }
    await _emit(event_sink, 'final_response', payload)

    return ChatResponse(
        trace_ref=ledger.trace_ref,
        intent='classification_conflict',
        answer=answer,
        badge=badge,
        evidence=[],
        tools_called=[],
        skills_invoked=[],
        cost_usd=0.0,
        total_tokens=0,
        latency_ms=latency_ms,
        provider=provider_name,
        model='',
        route=rules_decision.route,
        route_confidence=rules_decision.confidence,
        route_source='conflict',
        used_external_llm=_external_llm_used('', '', model_decision.source),
        resolution_required=resolution,
        query_redacted=query_redacted,
    )


async def _finalise_blocked(
    session: AsyncSession,
    ledger: ledger_mod.Ledger,
    query: str,
    query_redacted: str,
    provider_name: str,
    conversation_ref: str,
    *,
    badge: str,
    answer: str,
    intent: str,
    started_ms: int,
    llm_response: Any | None = None,
) -> ChatResponse:
    prompt_tokens = llm_response.prompt_tokens if llm_response else 0
    completion_tokens = llm_response.completion_tokens if llm_response else 0
    model = llm_response.model if llm_response else ''
    llm_latency = llm_response.latency_ms if llm_response else 0
    cost = compute_cost(provider_name, prompt_tokens, completion_tokens)
    ledger.event('final_response', 'blocked.response',
                 {'len': len(answer), 'evidence': [], 'status': badge,
                  'prompt_tokens': prompt_tokens,
                  'completion_tokens': completion_tokens,
                  'total_tokens': prompt_tokens + completion_tokens,
                  'cost_usd': cost},
                 latency_ms=llm_latency)
    await repo.conversation_upsert(session, conversation_ref, ledger.username, query[:200])
    await ledger_mod.persist(
        ledger=ledger, session=session, conversation_ref=conversation_ref,
        user_query=query, user_query_redacted=query_redacted,
        detected_intent=intent, final_answer=answer, final_status=badge,
        llm_provider=provider_name, llm_model=model,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        estimated_cost_usd=cost, llm_latency_ms=llm_latency,
        tool_latency_ms=0, otel_trace_id=current_trace_id_hex(),
    )
    return ChatResponse(
        trace_ref=ledger.trace_ref, intent=intent, answer=answer, badge=badge,
        cost_usd=cost, total_tokens=prompt_tokens + completion_tokens,
        latency_ms=int(time.time() * 1000) - started_ms,
        provider=provider_name, model=model, query_redacted=query_redacted,
    )
