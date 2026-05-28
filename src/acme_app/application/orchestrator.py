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
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.application.adversarial import check_query, validate_step, validate_step_arguments
from acme_app.application.planner import create_plan
from acme_app.application.propose_confirm import build_proposed_action, get_pending_action, stage_pending_action
from acme_app.application.prompts import NARRATION_PREAMBLE
from acme_app.application.schemas import ChatResponse, ProposedActionDTO
from acme_app.domain.evidence import badge_for
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.llm.provider import get_provider
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

# Customers we know about, used to recover a "last customer in scope" hint when
# Redis has expired and we're rebuilding context from the durable PG history.
_KNOWN_CUSTOMERS = (
    'Northwind Energy', 'Contoso Retail',
    'Acme Logistics Europe', 'Acme Manufacturing Group',
    'BlueRiver Health', 'Skyline Aviation',
)
_ISSUE_REF_RE = re.compile(r'\bISS-\d{3,5}\b', re.I)


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
        enriched_query = (history_text + '\nCurrent message:\n' + query) if history_text else query

        with tracer.start_as_current_span('agent.plan'):
            try:
                plan, llm_plan_response = await create_plan(
                    enriched_query, provider_name,
                    context={'role': role, 'last_customer': (last_customer or {}).get('name') if last_customer else None,
                             'last_issue': last_issue},
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
        ledger.event('agent_plan', 'plan.created',
                     {'intent': plan.intent, 'steps': len(plan.steps), 'write_requested': plan.write_requested,
                      'narration_kind': plan.narration_kind})
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
        if plan.write_requested and not plan.requires_clarification:
            proposed_dto = await _maybe_propose(
                ledger=ledger, role=role, username=username,
                conversation_ref=conversation_ref, trace_ref=trace_ref,
                facts=facts, event_sink=event_sink,
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
        ledger.event('final_response', 'narration.complete',
                     {'model': narration_response.model, 'len': len(narration_response.text)},
                     latency_ms=narration_response.latency_ms)

        if plan.requires_clarification and plan.clarification_question:
            answer = plan.clarification_question
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

        prompt_tokens = llm_plan_response.prompt_tokens + narration_response.prompt_tokens
        completion_tokens = llm_plan_response.completion_tokens + narration_response.completion_tokens
        cost_usd = compute_cost(provider_name, prompt_tokens, completion_tokens)
        llm_latency = llm_plan_response.latency_ms + narration_response.latency_ms
        skill_output = facts.get('skill_output') or {}

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
            llm_model=narration_response.model or llm_plan_response.model,
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
        })

        return ChatResponse(
            trace_ref=trace_ref,
            intent=plan.intent,
            answer=answer,
            badge=badge,
            evidence=sorted(set(cumulative_evidence))[:20],
            proposed_action=proposed_dto,
            tools_called=tools_called,
            skills_invoked=skills_invoked,
            risk_level=skill_output.get('risk_level'),
            missing_information=skill_output.get('missing_information', []),
            cost_usd=cost_usd,
            total_tokens=prompt_tokens + completion_tokens,
            latency_ms=latency_ms,
            provider=provider_name,
            model=narration_response.model or llm_plan_response.model,
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
    if tool_name == 'get_customer_profile':
        facts['customer_profile'] = output
        if output.get('name'):
            cumulative_evidence.append(f'customer:{output.get("customer_id", output.get("name"))}')
            await conversation_memory.set_last_customer(username, conversation_ref, output)
    elif tool_name == 'get_open_issues':
        facts['open_issues'] = output.get('issues', [])
        for issue in output.get('issues', []):
            cumulative_evidence.append(f'issue:{issue.get("issue_ref")}')
        # Remember the first (highest-priority) open issue for follow-up refs.
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
