"""Repositories: thin async functions over SQLAlchemy core.

Kept procedural rather than class-based because there are no overlapping
behaviours to share. Each function is one query.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ping_db(session: AsyncSession) -> bool:
    await session.execute(text('SELECT 1'))
    return True


async def list_customers(session: AsyncSession, name_like: str | None = None) -> list[dict[str, Any]]:
    if name_like:
        rows = (await session.execute(
            text("SELECT id::text, name, region, tier FROM customers WHERE lower(name) LIKE lower(:p) ORDER BY name"),
            {'p': f'%{name_like}%'},
        )).all()
    else:
        rows = (await session.execute(text("SELECT id::text, name, region, tier FROM customers ORDER BY name"))).all()
    return [{'customer_id': r[0], 'name': r[1], 'region': r[2], 'tier': r[3]} for r in rows]


async def conversation_upsert(session: AsyncSession, conversation_ref: str, username: str, preview: str) -> None:
    await session.execute(text("""
        INSERT INTO conversations (id, conversation_ref, username, title, started_at, last_message_at, last_message_preview, message_count)
        VALUES (gen_random_uuid(), :ref, :u, :title, now(), now(), :preview, 1)
        ON CONFLICT (conversation_ref) DO UPDATE SET
            last_message_at = now(),
            last_message_preview = :preview,
            message_count = conversations.message_count + 1
    """), {'ref': conversation_ref, 'u': username, 'title': preview[:80], 'preview': preview[:200]})


async def conversation_list(session: AsyncSession, username: str) -> list[dict[str, Any]]:
    rows = (await session.execute(
        text("SELECT conversation_ref, title, last_message_at, last_message_preview, message_count "
             "FROM conversations WHERE username=:u AND deleted_at IS NULL "
             "ORDER BY last_message_at DESC LIMIT 50"),
        {'u': username},
    )).all()
    return [
        {
            'conversation_ref': r[0],
            'title': r[1],
            'last_message_at': r[2].isoformat() if r[2] else None,
            'preview': r[3],
            'message_count': r[4],
        }
        for r in rows
    ]


async def insert_trace(
    session: AsyncSession,
    *,
    trace_ref: str,
    conversation_ref: str | None,
    username: str,
    user_role: str,
    user_query: str,
    user_query_redacted: str,
    detected_intent: str | None,
    final_answer: str | None,
    final_status: str,
    llm_provider: str,
    llm_model: str,
    prompt_tokens: int,
    completion_tokens: int,
    estimated_cost_usd: float,
    llm_latency_ms: int,
    tool_latency_ms: int,
    total_latency_ms: int,
    otel_trace_id: str | None = None,
) -> uuid.UUID:
    trace_id = uuid.uuid4()
    conv_id_row = None
    if conversation_ref:
        conv_id_row = (await session.execute(
            text("SELECT id FROM conversations WHERE conversation_ref=:r"),
            {'r': conversation_ref},
        )).scalar()
    await session.execute(text("""
        INSERT INTO agent_traces (
            id, trace_ref, otel_trace_id, conversation_id, username, user_role,
            user_query, user_query_redacted, detected_intent, final_answer, final_status,
            llm_provider, llm_model, prompt_tokens, completion_tokens, total_tokens,
            estimated_cost_usd, llm_latency_ms, tool_latency_ms, total_latency_ms, created_at
        ) VALUES (
            :id, :tref, :otel, :conv, :u, :role,
            :q, :qr, :intent, :ans, :status,
            :prov, :model, :pt, :ct, :tt,
            :cost, :ll, :tl, :al, now()
        )
    """), {
        'id': trace_id, 'tref': trace_ref, 'otel': otel_trace_id, 'conv': conv_id_row,
        'u': username, 'role': user_role, 'q': user_query, 'qr': user_query_redacted,
        'intent': detected_intent, 'ans': final_answer, 'status': final_status,
        'prov': llm_provider, 'model': llm_model,
        'pt': prompt_tokens, 'ct': completion_tokens, 'tt': prompt_tokens + completion_tokens,
        'cost': Decimal(str(estimated_cost_usd)),
        'll': llm_latency_ms, 'tl': tool_latency_ms, 'al': total_latency_ms,
    })
    return trace_id


async def insert_trace_event(
    session: AsyncSession,
    *,
    trace_id: uuid.UUID,
    event_type: str,
    event_name: str,
    payload: dict[str, Any],
    status: str = 'ok',
    latency_ms: int | None = None,
) -> None:
    await session.execute(text("""
        INSERT INTO trace_events (id, trace_id, event_type, event_name, payload, latency_ms, status, created_at)
        VALUES (gen_random_uuid(), :t, :et, :en, CAST(:p AS jsonb), :lm, :s, now())
    """), {'t': trace_id, 'et': event_type, 'en': event_name, 'p': json.dumps(payload), 'lm': latency_ms, 's': status})


async def insert_tool_call_log(
    session: AsyncSession,
    *,
    trace_id: uuid.UUID,
    tool_name: str,
    input_json: dict[str, Any],
    output_summary: dict[str, Any],
    status: str,
    latency_ms: int,
    error_message: str | None = None,
) -> None:
    await session.execute(text("""
        INSERT INTO tool_call_logs (id, trace_id, tool_name, input_json, output_summary, status, latency_ms, error_message, created_at)
        VALUES (gen_random_uuid(), :t, :tn, CAST(:i AS jsonb), CAST(:o AS jsonb), :s, :lm, :e, now())
    """), {
        't': trace_id, 'tn': tool_name,
        'i': json.dumps(input_json), 'o': json.dumps(output_summary),
        's': status, 'lm': latency_ms, 'e': error_message,
    })


async def insert_rbac_decision(
    session: AsyncSession,
    *,
    trace_id: uuid.UUID | None,
    username: str,
    role: str,
    operation: str,
    resource: str,
    allowed: bool,
    reason: str,
) -> None:
    await session.execute(text("""
        INSERT INTO rbac_decisions (id, trace_id, username, role_name, operation, resource, allowed, reason, created_at)
        VALUES (gen_random_uuid(), :t, :u, :r, :op, :res, :a, :why, now())
    """), {'t': trace_id, 'u': username, 'r': role, 'op': operation, 'res': resource, 'a': allowed, 'why': reason})


async def list_traces(
    session: AsyncSession,
    limit: int = 50,
    username: str | None = None,
) -> list[dict[str, Any]]:
    where = 'WHERE username = :username' if username else ''
    params: dict[str, Any] = {'n': limit}
    if username:
        params['username'] = username
    rows = (await session.execute(text(f"""
        SELECT trace_ref, username, user_role, detected_intent, final_status,
               llm_provider, llm_model, total_tokens, estimated_cost_usd, total_latency_ms, created_at
        FROM agent_traces
        {where}
        ORDER BY created_at DESC LIMIT :n
    """), params)).all()
    return [
        {
            'trace_ref': r[0], 'username': r[1], 'role': r[2], 'intent': r[3], 'status': r[4],
            'provider': r[5], 'model': r[6], 'total_tokens': r[7], 'cost_usd': float(r[8] or 0),
            'latency_ms': r[9], 'created_at': r[10].isoformat() if r[10] else None,
        }
        for r in rows
    ]


async def get_trace(session: AsyncSession, trace_ref: str) -> dict[str, Any] | None:
    row = (await session.execute(text("""
        SELECT id, trace_ref, otel_trace_id, username, user_role, user_query, user_query_redacted,
               detected_intent, final_answer, final_status, llm_provider, llm_model,
               prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd,
               llm_latency_ms, tool_latency_ms, total_latency_ms, created_at
        FROM agent_traces WHERE trace_ref=:r
    """), {'r': trace_ref})).first()
    if row is None:
        return None
    trace_id = row[0]
    events = (await session.execute(text("""
        SELECT event_type, event_name, payload, latency_ms, status, created_at
        FROM trace_events WHERE trace_id=:t ORDER BY created_at
    """), {'t': trace_id})).all()
    tool_calls = (await session.execute(text("""
        SELECT tool_name, input_json, output_summary, status, latency_ms, error_message, created_at
        FROM tool_call_logs WHERE trace_id=:t ORDER BY created_at
    """), {'t': trace_id})).all()
    rbac = (await session.execute(text("""
        SELECT role_name, operation, resource, allowed, reason, created_at
        FROM rbac_decisions WHERE trace_id=:t ORDER BY created_at
    """), {'t': trace_id})).all()
    normalised_events = []
    for e in events:
        payload = e[2] if isinstance(e[2], dict) else {}
        normalised_events.append({
            'event_type': e[0],
            'event_name': e[1],
            'payload': payload,
            'latency_ms': e[3],
            'status': e[4],
            'created_at': e[5].isoformat(),
        })
    evidence = _events_to_evidence(normalised_events)

    return {
        'trace_ref': row[1], 'otel_trace_id': row[2], 'username': row[3], 'role': row[4],
        'user_query': row[5], 'user_query_redacted': row[6],
        'detected_intent': row[7], 'final_answer': row[8], 'final_status': row[9],
        'provider': row[10], 'model': row[11],
        'prompt_tokens': row[12], 'completion_tokens': row[13], 'total_tokens': row[14],
        'cost_usd': float(row[15] or 0),
        'llm_latency_ms': row[16], 'tool_latency_ms': row[17], 'total_latency_ms': row[18],
        'created_at': row[19].isoformat() if row[19] else None,
        'events': normalised_events,
        'evidence': evidence,
        'tool_calls': [
            {'tool_name': t[0], 'input': t[1], 'output_summary': t[2], 'status': t[3], 'latency_ms': t[4], 'error': t[5], 'created_at': t[6].isoformat()}
            for t in tool_calls
        ],
        'rbac_decisions': [
            {'role': r[0], 'operation': r[1], 'resource': r[2], 'allowed': r[3], 'reason': r[4], 'created_at': r[5].isoformat()}
            for r in rbac
        ],
    }


async def insert_eval_run(session: AsyncSession, *, eval_run_ref: str, llm_provider: str, llm_model: str) -> uuid.UUID:
    run_id = uuid.uuid4()
    await session.execute(text("""
        INSERT INTO eval_runs (id, eval_run_ref, llm_provider, llm_model, started_at)
        VALUES (:id, :ref, :p, :m, now())
    """), {'id': run_id, 'ref': eval_run_ref, 'p': llm_provider, 'm': llm_model})
    return run_id


async def finalise_eval_run(session: AsyncSession, *, run_id: uuid.UUID, cases_total: int, cases_passed: int, total_cost_usd: float) -> None:
    await session.execute(text("""
        UPDATE eval_runs SET completed_at=now(), cases_total=:t, cases_passed=:p, total_cost_usd=:c WHERE id=:id
    """), {'id': run_id, 't': cases_total, 'p': cases_passed, 'c': Decimal(str(total_cost_usd))})


async def insert_eval_result(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    case_id: str,
    query: str,
    role: str,
    expected_tools: list[str],
    actual_tools: list[str],
    tool_selection_pass: bool,
    grounding_pass: bool,
    rbac_pass: bool,
    action_reasonableness_pass: bool,
    adversarial_pass: bool | None,
    latency_ms: int,
    cost_usd: float,
    notes: str,
) -> None:
    await session.execute(text("""
        INSERT INTO eval_results (
            id, eval_run_id, case_id, query, role_name, expected_tools, actual_tools,
            tool_selection_pass, grounding_pass, rbac_pass, action_reasonableness_pass,
            adversarial_pass, latency_ms, cost_usd, notes, created_at
        ) VALUES (
            gen_random_uuid(), :run, :c, :q, :r, :et, :at,
            :tsp, :gp, :rp, :arp, :ap, :lm, :cu, :n, now()
        )
    """), {
        'run': run_id, 'c': case_id, 'q': query, 'r': role,
        'et': expected_tools, 'at': actual_tools,
        'tsp': tool_selection_pass, 'gp': grounding_pass, 'rp': rbac_pass,
        'arp': action_reasonableness_pass, 'ap': adversarial_pass,
        'lm': latency_ms, 'cu': Decimal(str(cost_usd)), 'n': notes,
    })


async def soft_delete_conversation(
    session: AsyncSession, conversation_ref: str, username: str,
) -> bool:
    """Mark a conversation hidden from the sidebar. Trace data is untouched.

    Returns True if a row was updated, False if the conversation didn't exist
    or belonged to a different user. The constraint on username is the
    minimum authz: a user can only soft-delete conversations they own.
    """
    result = await session.execute(
        text("UPDATE conversations SET deleted_at = now() "
             "WHERE conversation_ref = :r AND username = :u AND deleted_at IS NULL "
             "RETURNING conversation_ref"),
        {'r': conversation_ref, 'u': username},
    )
    return result.first() is not None


async def get_conversation_history(session: AsyncSession, conversation_ref: str) -> list[dict[str, Any]]:
    """Return the full message history for a conversation_ref, ordered oldest→newest.

    Each row is a (user_query, final_answer) pair plus trace metadata and a
    pre-built list of plan steps (rebuilt from trace_events) so the historical
    view can show the same planning card the live SSE stream showed at the
    time of the turn.
    """
    rows = (await session.execute(text("""
        SELECT t.id, t.user_query_redacted, t.final_answer, t.trace_ref, t.final_status,
               t.estimated_cost_usd, t.total_tokens, t.total_latency_ms, t.llm_provider,
               t.created_at, t.detected_intent, t.llm_model
        FROM agent_traces t
        WHERE t.conversation_id = (SELECT id FROM conversations WHERE conversation_ref = :ref)
        ORDER BY t.created_at ASC
    """), {'ref': conversation_ref})).all()

    out: list[dict[str, Any]] = []
    for r in rows:
        trace_id = r[0]
        events = (await session.execute(text("""
            SELECT event_type, event_name, payload, latency_ms, status
            FROM trace_events
            WHERE trace_id = :t
            ORDER BY created_at ASC
        """), {'t': trace_id})).all()
        event_dicts = [
            {'event_type': e[0], 'event_name': e[1], 'payload': e[2] or {},
             'latency_ms': e[3], 'status': e[4]}
            for e in events
        ]
        plan_steps = _events_to_plan_steps(event_dicts)
        choice_kind, choice_options = _events_to_pending_choices(event_dicts, r[4])
        evidence = _events_to_evidence(event_dicts)
        out.append({
            'user_query': r[1],
            'answer': r[2] or '',
            'trace_ref': r[3],
            'badge': r[4],
            'cost_usd': float(r[5] or 0),
            'total_tokens': r[6] or 0,
            'latency_ms': r[7] or 0,
            'provider': r[8],
            'created_at': r[9].isoformat() if r[9] else None,
            'intent': r[10],
            'model': r[11],
            'plan_steps': plan_steps,
            'evidence': evidence,
            'choice_kind': choice_kind,
            'choice_options': choice_options,
        })
    return out


def _normalise_evidence_items(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    evidence: list[str] = []
    for item in raw:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        evidence.append(value)
    return evidence


def _events_to_evidence(events: list[dict[str, Any]]) -> list[str]:
    """Return the evidence list attached to the decision outcome.

    The final response is the canonical source for read-only answers. Action
    proposal and confirmation events are fallbacks for older traces and write
    flows where the action payload itself carries the supporting records.
    """
    for ev in reversed(events):
        if ev.get('event_type') == 'final_response':
            evidence = _normalise_evidence_items((ev.get('payload') or {}).get('evidence'))
            if evidence:
                return evidence
    for ev in reversed(events):
        if ev.get('event_type') in {'action_proposed', 'action_confirmed'}:
            evidence = _normalise_evidence_items((ev.get('payload') or {}).get('evidence'))
            if evidence:
                return evidence
    return []


def _events_to_pending_choices(
    events: list[dict[str, Any]],
    final_status: str | None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Restore unresolved inline choice controls after a page refresh."""
    if final_status == 'Resolution Required':
        for ev in events:
            if ev['event_type'] == 'agent_plan' and ev['event_name'] == 'classification.conflict':
                payload = ev['payload'] or {}
                rules_route = payload.get('rules_route') or 'clarification'
                model_route = payload.get('model_route') or 'clarification'
                return 'resolution', [
                    {
                        'key': 'rules',
                        'label': f'Use rules: {rules_route}',
                        'route': rules_route,
                        'reason': payload.get('rules_reason') or '',
                    },
                    {
                        'key': 'model',
                        'label': f'Use model: {model_route}',
                        'route': model_route,
                        'reason': payload.get('model_reason') or '',
                    },
                    {
                        'key': 'other',
                        'label': 'Other / clarify',
                        'route': 'clarification',
                        'reason': 'Ask the user to clarify the intended workflow.',
                    },
                ]
    if final_status == 'Clarification Required':
        for ev in events:
            if ev['event_type'] == 'agent_plan' and ev['event_name'] in {
                'ambiguous_customer.preplan_detected',
                'ambiguous_customer.detected',
            }:
                candidates = (ev['payload'] or {}).get('candidates') or []
                options = [
                    {'label': str(candidate), 'value': str(candidate), 'description': ''}
                    for candidate in candidates
                    if str(candidate).strip()
                ]
                if options:
                    return 'clarification', options
    return None, []


def _events_to_plan_steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce a trace's raw event stream into the same step list the SSE
    stream produces for the live plan-quiet card: a Planning row, then one
    row per tool/skill, plus a few semantic markers (rbac_denied, blocked).
    """
    steps: list[dict[str, Any]] = []
    for ev in events:
        et, en, payload = ev['event_type'], ev['event_name'], ev['payload'] or {}
        if et == 'agent_plan' and en == 'plan.created':
            steps.append({
                'kind': 'planning',
                'label': 'Planning',
                'state': 'ok',
                'intent': payload.get('intent'),
                'step_count': payload.get('steps'),
            })
        elif et == 'agent_plan' and en == 'classification.conflict':
            steps.append({
                'kind': 'planning',
                'label': 'Planning',
                'state': 'fail',
                'intent': 'classification_conflict',
                'step_count': 0,
                'reason': f"rules {payload.get('rules_route')} vs model {payload.get('model_route')}",
            })
        elif et == 'agent_plan' and en == 'ambiguous_customer.preplan_detected':
            steps.append({
                'kind': 'planning',
                'label': 'Planning',
                'state': 'ok',
                'intent': 'disambiguate_customer',
                'step_count': 0,
                'reason': 'ambiguous customer',
            })
        elif et == 'tool_call' and en.endswith('.complete'):
            tool_name = en.removeprefix('tool.').removesuffix('.complete')
            steps.append({
                'kind': 'tool',
                'label': tool_name,
                'state': 'ok',
                'latency_ms': ev.get('latency_ms'),
            })
        elif et == 'skill_invocation' and en.endswith('.complete'):
            skill_name = en.removeprefix('skill.').removesuffix('.complete')
            steps.append({
                'kind': 'skill',
                'label': f'skill: {skill_name}',
                'state': 'ok',
                'risk_level': payload.get('risk_level'),
                'latency_ms': ev.get('latency_ms'),
            })
        elif et == 'rbac_decision' and not payload.get('allowed', True):
            steps.append({
                'kind': 'rbac',
                'label': 'policy.rbac_check',
                'state': 'fail',
                'reason': payload.get('reason'),
            })
        elif et == 'adversarial' and ev['status'] == 'blocked':
            steps.append({
                'kind': 'block',
                'label': 'adversarial.check',
                'state': 'fail',
                'reason': ', '.join(payload.get('flags', [])),
            })
        elif et == 'error' and en.startswith('plan.step.rejected.'):
            tool = en.removeprefix('plan.step.rejected.')
            steps.append({
                'kind': 'tool',
                'label': tool,
                'state': 'queued',
                'reason': payload.get('reason'),
            })
    return steps


async def get_latest_pending_proposal(session: AsyncSession, conversation_ref: str) -> dict[str, Any] | None:
    """Look up the most recent action_proposed in this conversation that has
    not yet been turned into a row in next_actions (i.e. user never confirmed).

    Returns the proposal payload (action_type, issue_ref, priority, title,
    description, rationale, evidence, due_at, customer_*, idempotency_key,
    trace_ref) without a confirmation_token — the caller mints a fresh one.
    """
    row = (await session.execute(text("""
        SELECT e.payload, e.created_at, t.trace_ref
        FROM trace_events e
        JOIN agent_traces t ON t.id = e.trace_id
        WHERE e.event_type = 'action_proposed'
          AND t.conversation_id = (SELECT id FROM conversations WHERE conversation_ref = :r)
        ORDER BY e.created_at DESC
        LIMIT 1
    """), {'r': conversation_ref})).first()
    if row is None:
        return None
    payload = row[0]
    if not isinstance(payload, dict):
        return None
    idem = payload.get('idempotency_key')
    if idem:
        existing = (await session.execute(
            text('SELECT 1 FROM next_actions WHERE idempotency_key=:k'),
            {'k': idem},
        )).first()
        if existing is not None:
            return None  # already confirmed → not pending
    payload.setdefault('trace_ref', row[2])
    return payload


async def db_counts(session: AsyncSession) -> dict[str, int]:
    tables = ['customers', 'issues', 'issue_updates', 'next_actions', 'conversations', 'agent_traces', 'trace_events', 'tool_call_logs', 'rbac_decisions', 'eval_runs', 'eval_results']
    out: dict[str, int] = {}
    for t in tables:
        row = (await session.execute(text(f"SELECT COUNT(*) FROM {t}"))).scalar()
        out[t] = int(row or 0)
    return out
