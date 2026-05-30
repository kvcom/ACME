"""Agent orchestrator.

The execution loop in section 12.3 of the plan, condensed:

  1. adversarial check (length + patterns)
  2. PII redaction
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

import logging
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.application.adversarial import check_query, validate_step, validate_step_arguments
from acme_app.application.planner import create_plan
from acme_app.application.propose_confirm import build_proposed_action, get_pending_action, stage_pending_action
from acme_app.application.prompts import NARRATION_PREAMBLE
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
from acme_app.infrastructure.mcp_client.client import MCPClient, MCPClientError
from acme_app.infrastructure.mcp_client.schemas import WRITE_TOOLS
from acme_app.infrastructure.redis_memory import conversation_memory
from acme_app.observability import decision_ledger as ledger_mod
from acme_app.observability.cost_calculator import compute as compute_cost
from acme_app.observability.otel import current_trace_id_hex, get_tracer
from acme_app.policy.action_guard import can_propose, verify_confirmation_token
from acme_app.policy.pii_redactor import redact
from acme_app.policy.rbac import check as rbac_check
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
    r'add|make|submit|write up|set up'
    r')\b',
    re.I,
)
_OPEN_WRITE_INTENT_RE = re.compile(
    r'\b(open|file|log|raise)\s+(an?\s+)?'
    r'(action|ticket|case|issue|task|next action|recovery plan)\b',
    re.I,
)

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


def _new_trace_ref() -> str:
    return f'TRC-{uuid.uuid4().hex[:8].upper()}'


def _summarise(output: dict[str, Any]) -> dict[str, Any]:
    return ledger_mod.summarise_output(output)


def _explicit_write_intent(query: str) -> bool:
    """Only stage proposed actions when the user's wording asks for a write."""
    return bool(_WRITE_INTENT_RE.search(query) or _OPEN_WRITE_INTENT_RE.search(query))


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
    """Expand a customer-choice click into the original unresolved request.

    Small local models often treat a clarification answer like "Acme
    Manufacturing Group" as the whole request. This helper preserves the
    user's choice while making the intended prior question explicit.
    """
    choice = query.strip()
    if not choice or '?' in choice or len(choice.split()) > 6:
        return None

    previous_user: str | None = None
    saw_customer_clarification = False
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
            continue
        if saw_customer_clarification and turn.get('role') == 'user':
            previous_user = text
            break

    if not previous_user:
        return None

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

        ok_length, adversarial, flags = check_query(query)
        ledger.event('auth', 'auth.validate_role', {'role': role, 'username': username})
        ledger.event('adversarial', 'adversarial.check',
                     {'flags': flags, 'length_ok': ok_length, 'detected': adversarial},
                     status='blocked' if (adversarial or not ok_length) else 'ok')
        await _emit(event_sink, 'adversarial', {'flags': flags, 'detected': adversarial})

        query_redacted = redact(query)
        ledger.event('pii', 'pii.redact',
                     {'changed': query_redacted != query, 'length': len(query)})

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
                          'last_issue': last_issue})

        # Build a transcript snippet for the LLM so short follow-ups ("yes",
        # "that one", pronouns) resolve against the actual previous turn.
        history_text = ''
        if recent_turns:
            lines = []
            for turn in recent_turns[-6:]:
                who = 'User' if turn.get('role') == 'user' else 'Assistant'
                txt = (turn.get('text') or '').strip().replace('\n', ' ')
                if txt:
                    lines.append(f'{who}: {txt[:300]}')
            if lines:
                history_text = 'Recent conversation:\n' + '\n'.join(lines) + '\n\n'
        if last_customer and last_customer.get('name'):
            history_text += f'Last customer in scope: {last_customer["name"]}\n'
        if last_issue:
            history_text += f'Last issue in scope: {last_issue}\n'
        if pending:
            history_text += (
                f'There is a pending proposed action awaiting user confirmation: '
                f'{pending.get("action_type")} on {pending.get("issue_ref")}.\n'
            )
        effective_query = _clarification_followup_query(query, recent_turns) or query
        enriched_query = (history_text + '\nCurrent message:\n' + effective_query) if history_text else effective_query

        plan_context = {
            'role': role,
            'last_customer': (last_customer or {}).get('name') if last_customer else None,
            'last_issue': last_issue,
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
                ambiguity=preplan_ambig,
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
                    ledger.event('agent_plan', 'classification.human_resolved',
                                 {'route': route_decision.route})
                plan, llm_plan_response = await create_plan(
                    enriched_query, provider_name,
                    context=plan_context,
                )
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
                      'model': llm_plan_response.model,
                      'prompt_tokens': llm_plan_response.prompt_tokens,
                      'completion_tokens': llm_plan_response.completion_tokens,
                      'total_tokens': llm_plan_response.prompt_tokens + llm_plan_response.completion_tokens,
                      'cost_usd': plan_event_cost})
        await _emit(event_sink, 'plan', {'intent': plan.intent, 'steps_count': len(plan.steps),
                                          'narration_kind': plan.narration_kind})

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
            # Sanity gate: a confirm intent only makes sense when there's
            # actually a pending action AND the user's message reads like a
            # bare affirmation. Smaller models occasionally fire confirm
            # spuriously on long questions; catch that and demote to a normal
            # plan so we still answer the user usefully.
            short_affirmations = {
                'yes', 'yeah', 'yep', 'sure', 'ok', 'okay', 'confirm',
                'go ahead', 'do it', 'approve', 'create it', 'create',
                'proceed', 'please confirm',
            }
            normalised = query.strip().lower().rstrip('.!')
            looks_like_confirm = (
                normalised in short_affirmations
                or normalised.startswith('confirm')
                or normalised.startswith('yes ')
                or len(normalised.split()) <= 3
            )
            looks_like_confirm = looks_like_confirm and ('?' not in query)
            if pending and looks_like_confirm:
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
                await _emit(event_sink, 'tool_start', {'tool': step.name, 'args': step.arguments})
                with tracer.start_as_current_span(f'mcp.tool.{step.name}'):
                    start_t = time.perf_counter()
                    try:
                        output = await mcp.call_tool(step.name, step.arguments)
                        latency = int((time.perf_counter() - start_t) * 1000)
                        tool_latency_total += latency
                        ledger.tool(step.name, step.arguments, _summarise(output), 'ok', latency)
                        ledger.event('tool_call', f'tool.{step.name}.complete',
                                     {'keys': list(output.keys())[:8]}, latency_ms=latency)
                        await _emit(event_sink, 'tool_complete',
                                    {'tool': step.name, 'summary': _summarise(output), 'latency_ms': latency})
                        await _ingest_tool_output(step.name, output, facts, cumulative_evidence,
                                                   username, conversation_ref)
                    except MCPClientError as exc:
                        latency = int((time.perf_counter() - start_t) * 1000)
                        ledger.tool(step.name, step.arguments, {'error': str(exc)}, 'error', latency, str(exc))
                        ledger.event('error', f'tool.{step.name}.error', {'error': str(exc)},
                                     status='error', latency_ms=latency)
                        await _emit(event_sink, 'tool_error', {'tool': step.name, 'error': str(exc)})
                tools_called.append(step.name)

            elif step.step_type == 'skill':
                await _emit(event_sink, 'skill_start', {'skill': step.name})
                with tracer.start_as_current_span(f'skill.{step.name}'):
                    start_t = time.perf_counter()
                    skill_output = _invoke_skill(step.name, step.arguments, facts, role)
                    latency = int((time.perf_counter() - start_t) * 1000)
                ledger.event('skill_invocation', f'skill.{step.name}.complete',
                             {'risk_level': skill_output.get('risk_level'),
                              'recommended_next_action': skill_output.get('recommended_next_action', {}).get('action_type')},
                             latency_ms=latency)
                await _emit(event_sink, 'skill_complete',
                            {'skill': step.name, 'risk_level': skill_output.get('risk_level'),
                             'latency_ms': latency})
                facts['skill_output'] = skill_output
                facts['skill_name'] = step.name
                skills_invoked.append(step.name)
                cumulative_evidence.extend(skill_output.get('evidence', []))

        proposed_dto: ProposedActionDTO | None = None
        write_intent = _explicit_write_intent(query)
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
        facts['conversation_history'] = history_text or None
        try:
            narration_response = await narration_provider.narrate(NARRATION_PREAMBLE, enriched_query, facts)
        except Exception as exc:
            ledger.event('error', 'llm.narrate.unavailable', {'error': str(exc)}, status='error')
            # Soft-fall back to a templated answer so the user still sees something useful.
            from acme_app.infrastructure.llm.providers.base import LLMResponse
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
        evidence_list = sorted(set(cumulative_evidence))[:20]
        ledger.event('final_response', 'narration.complete',
                     {'model': narration_response.model, 'len': len(narration_response.text),
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
        elif proposed_dto is not None:
            answer = narration_response.text
            badge = 'Action Proposed'
        elif plan.intent == 'adversarial':
            answer = narration_response.text
            badge = 'Adversarial Input Blocked'
        else:
            answer = narration_response.text
            has_evidence = bool(cumulative_evidence) or bool(facts.get('skill_output'))
            badge = badge_for(has_evidence=has_evidence)

        if not plan.requires_clarification and _needs_customer_status_fallback(answer, facts):
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
            risk_level=skill_output.get('risk_level'),
            missing_information=skill_output.get('missing_information', []),
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
        if output.get('multiple_matches'):
            # Only flag ambiguity if no earlier call already resolved a customer.
            if not have_resolved_customer:
                facts['customer_profile'] = output
                facts['ambiguous_customer'] = {
                    'queried': output.get('queried'),
                    'matches': output.get('matches', []),
                }
        elif output.get('name'):
            facts['customer_profile'] = output
            # A successful unique match clears any earlier ambiguity hint —
            # the agent has now confirmed which customer is in scope.
            facts.pop('ambiguous_customer', None)
            cumulative_evidence.append(f'customer:{output.get("customer_id", output.get("name"))}')
            await conversation_memory.set_last_customer(username, conversation_ref, output)
        else:
            # not_found path
            facts['customer_profile'] = output
    elif tool_name == 'get_open_issues':
        if output.get('multiple_matches'):
            if not have_resolved_customer:
                facts['ambiguous_customer'] = {
                    'queried': output.get('queried'),
                    'matches': output.get('matches', []),
                }
            facts['open_issues'] = []
        else:
            facts['open_issues'] = output.get('issues', [])
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
        return skill(
            customer=facts.get('customer_profile') or {'name': arguments.get('customer_name', '')},
            issues=facts.get('open_issues', []),
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
            answer='I do not have a pending action to confirm. Please re-issue the request.',
            intent='no_pending_action', started_ms=started_ms,
            llm_response=llm_plan_response,
        )
    ok_tok, why_tok, _ = verify_confirmation_token(pending['confirmation_token'])
    ledger.event('action_validation', 'token.verify',
                 {'ok': ok_tok, 'reason': why_tok},
                 status='ok' if ok_tok else 'error')
    decision = rbac_check(role, 'create_action')
    ledger.rbac(role=role, operation='create_action', resource=pending['action_type'],
                allowed=decision.allowed, reason=decision.reason)
    if not (ok_tok and decision.allowed):
        return await _finalise_blocked(
            session, ledger, query, query_redacted, provider_name, conversation_ref,
            badge='Permission Denied',
            answer=f'Cannot confirm action: {why_tok if not ok_tok else decision.reason}',
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
            answer = f'Action {result["action_ref"]} created and assigned to {role}.'
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
        narration_response = await narration_provider.narrate(
            NARRATION_PREAMBLE,
            f'{history_text}\nCurrent message:\n{query}' if history_text else query,
            facts,
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
