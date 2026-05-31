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


async def list_users(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(text(
        "SELECT id::text, username, email, display_name FROM users ORDER BY username"
    ))).all()
    return [
        {'user_id': r[0], 'username': r[1], 'email': r[2], 'display_name': r[3]}
        for r in rows
    ]


def _serialise_record(row: Any) -> dict[str, Any]:
    record = dict(row._mapping)
    for key, value in list(record.items()):
        if isinstance(value, datetime):
            record[key] = value.isoformat()
        elif isinstance(value, Decimal):
            record[key] = float(value)
        elif isinstance(value, uuid.UUID):
            record[key] = str(value)
    return record


async def get_evidence_record(session: AsyncSession, kind: str, identifier: str) -> dict[str, Any] | None:
    """Resolve an evidence reference to its source database row.

    This is intentionally allowlisted by evidence kind rather than exposing
    arbitrary table/column selection to the browser.
    """
    kind = kind.strip().lower()
    identifier = identifier.strip()
    if not identifier:
        return None

    if kind == 'issue':
        row = (await session.execute(text("""
            SELECT id::text, issue_ref, customer_id::text, title, description, severity,
                   status, sla_status, owner, opened_at, updated_at
            FROM issues
            WHERE issue_ref = :identifier
            LIMIT 1
        """), {'identifier': identifier})).first()
        table = 'issues'
    elif kind == 'update':
        row = (await session.execute(text("""
            SELECT u.id::text, u.issue_id::text, i.issue_ref, u.update_text,
                   u.update_type, u.created_by, u.created_at
            FROM issue_updates u
            JOIN issues i ON i.id = u.issue_id
            WHERE u.id::text = :identifier
            LIMIT 1
        """), {'identifier': identifier})).first()
        table = 'issue_updates'
    elif kind == 'customer':
        row = (await session.execute(text("""
            SELECT id::text, name, industry, tier, region, customer_timezone,
                   account_owner, status, created_at
            FROM customers
            WHERE id::text = :identifier OR name = :identifier
            LIMIT 1
        """), {'identifier': identifier})).first()
        table = 'customers'
    elif kind in {'action', 'next_action'}:
        row = (await session.execute(text("""
            SELECT id::text, action_ref, customer_id::text, issue_id::text, action_type,
                   title, description, priority, status, owner_role, owner_name, due_at,
                   rationale, evidence_json, created_by, created_by_role,
                   idempotency_key, created_at, updated_at, completed_at
            FROM next_actions
            WHERE action_ref = :identifier OR id::text = :identifier
            LIMIT 1
        """), {'identifier': identifier})).first()
        table = 'next_actions'
    elif kind == 'user':
        row = (await session.execute(text("""
            SELECT u.id::text, u.username, u.email, u.display_name,
                   u.keycloak_subject, u.is_active, u.created_at, u.deleted_at,
                   COALESCE(array_agg(ur.role_name ORDER BY ur.role_name)
                            FILTER (WHERE ur.role_name IS NOT NULL), ARRAY[]::text[]) AS roles
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            WHERE u.username = :identifier OR u.id::text = :identifier
            GROUP BY u.id, u.username, u.email, u.display_name,
                     u.keycloak_subject, u.is_active, u.created_at, u.deleted_at
            LIMIT 1
        """), {'identifier': identifier})).first()
        table = 'users + user_roles'
    elif kind in {'action_policy', 'action_catalogue'}:
        row = (await session.execute(text("""
            SELECT action_type, label, description, allowed_roles, required_fields,
                   side_effect_level, requires_confirmation, is_active
            FROM action_catalogue
            WHERE action_type = :identifier
            LIMIT 1
        """), {'identifier': identifier})).first()
        table = 'action_catalogue'
    else:
        return None

    if row is None:
        return None
    return {'table': table, 'record': _serialise_record(row)}


async def conversation_upsert(session: AsyncSession, conversation_ref: str, username: str, preview: str) -> None:
    # D-017: also populate the live user_id FK alongside the username snapshot.
    # The sub-select resolves it from username so callers don't need to know;
    # NULL is fine when the username isn't a system user (legacy data only).
    await session.execute(text("""
        INSERT INTO conversations (id, conversation_ref, user_id, username, title, started_at, last_message_at, last_message_preview, message_count)
        VALUES (
            gen_random_uuid(), :ref,
            (SELECT id FROM users WHERE username = :u),
            :u, :title, now(), now(), :preview, 1
        )
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
    # D-017: user_id is the live FK; username + user_role stay as historical
    # snapshots. We resolve user_id from username in the same INSERT so callers
    # don't need to thread it through.
    await session.execute(text("""
        INSERT INTO agent_traces (
            id, trace_ref, otel_trace_id, conversation_id,
            user_id, username, user_role,
            user_query, user_query_redacted, detected_intent, final_answer, final_status,
            llm_provider, llm_model, prompt_tokens, completion_tokens, total_tokens,
            estimated_cost_usd, llm_latency_ms, tool_latency_ms, total_latency_ms, created_at
        ) VALUES (
            :id, :tref, :otel, :conv,
            (SELECT id FROM users WHERE username = :u), :u, :role,
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
    sequence: int | None = None,
    created_at_ms: int | None = None,
) -> None:
    stored_payload = dict(payload)
    if sequence is not None:
        stored_payload.setdefault('_sequence', sequence)
    await session.execute(text("""
        INSERT INTO trace_events (id, trace_id, event_type, event_name, payload, latency_ms, status, created_at)
        VALUES (
            gen_random_uuid(), :t, :et, :en, CAST(:p AS jsonb), :lm, :s,
            CASE
                WHEN CAST(:created_at_ms AS bigint) IS NULL THEN now()
                ELSE to_timestamp(CAST(:created_at_ms AS double precision) / 1000.0)
                     + ((COALESCE(CAST(:sequence AS integer), 0)) * interval '1 microsecond')
            END
        )
    """), {
        't': trace_id, 'et': event_type, 'en': event_name,
        'p': json.dumps(stored_payload), 'lm': latency_ms, 's': status,
        'sequence': sequence, 'created_at_ms': created_at_ms,
    })


async def get_trace_id_by_ref(session: AsyncSession, trace_ref: str) -> uuid.UUID | None:
    if not trace_ref:
        return None
    return (await session.execute(
        text('SELECT id FROM agent_traces WHERE trace_ref = :trace_ref'),
        {'trace_ref': trace_ref},
    )).scalar()


async def update_trace_outcome(
    session: AsyncSession,
    *,
    trace_ref: str,
    final_status: str,
) -> None:
    if not trace_ref:
        return
    await session.execute(text("""
        UPDATE agent_traces
        SET final_status = :final_status
        WHERE trace_ref = :trace_ref
    """), {'trace_ref': trace_ref, 'final_status': final_status})


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
        SELECT event_type, event_name, payload, latency_ms, status, created_at, id::text
        FROM trace_events WHERE trace_id=:t
        ORDER BY created_at, COALESCE((payload->>'_sequence')::int, 2147483647), id
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
    normalised_events.sort(key=_trace_event_sort_key)
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


def _trace_event_sort_key(event: dict[str, Any]) -> tuple[str, int, int]:
    payload = event.get('payload') or {}
    sequence = payload.get('_sequence')
    if isinstance(sequence, int):
        return (event.get('created_at') or '', sequence, 0)
    if isinstance(sequence, str) and sequence.isdigit():
        return (event.get('created_at') or '', int(sequence), 0)
    return (event.get('created_at') or '', 2147483647, _trace_event_semantic_rank(event))


def _trace_event_semantic_rank(event: dict[str, Any]) -> int:
    event_type = event.get('event_type') or ''
    event_name = event.get('event_name') or ''
    if event_name == 'auth.validate_role':
        return 10
    if event_type == 'adversarial':
        return 20
    if event_type == 'pii':
        return 30
    if event_name == 'outbound_llm.minimized':
        return 40
    if event_type == 'memory':
        return 45
    if event_type == 'agent_plan':
        return 50
    if event_type == 'tool_call':
        return 60
    if event_type == 'skill_invocation':
        return 70
    if event_type == 'rbac_decision':
        return 75
    if event_type.startswith('action'):
        return 80
    if event_name == 'outbound_llm.narration_payload':
        return 90
    if event_type == 'final_response':
        return 100
    if event_type == 'error':
        return 110
    return 999


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
    # D-017: connect the eval island to the identity graph. Each eval case
    # runs under one of three permanent eval-persona users keyed by role.
    eval_persona = {
        'sales_user':   'eval.sales',
        'support_user': 'eval.support',
        'admin':        'eval.admin',
    }.get(role)
    await session.execute(text("""
        INSERT INTO eval_results (
            id, eval_run_id, case_id, query, user_id, role_name,
            expected_tools, actual_tools,
            tool_selection_pass, grounding_pass, rbac_pass, action_reasonableness_pass,
            adversarial_pass, latency_ms, cost_usd, notes, created_at
        ) VALUES (
            gen_random_uuid(), :run, :c, :q,
            (SELECT id FROM users WHERE username = :persona),
            :r, :et, :at,
            :tsp, :gp, :rp, :arp, :ap, :lm, :cu, :n, now()
        )
    """), {
        'run': run_id, 'c': case_id, 'q': query, 'persona': eval_persona, 'r': role,
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
        action_outcome = _events_to_action_outcome(event_dicts)
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
            'action_outcome': action_outcome,
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
    evidence: list[str] = []
    seen: set[str] = set()

    def add(items: list[str]) -> None:
        for item in items:
            if item not in seen:
                seen.add(item)
                evidence.append(item)

    for ev in reversed(events):
        if ev.get('event_type') == 'final_response':
            add(_normalise_evidence_items((ev.get('payload') or {}).get('evidence')))
            break

    for ev in events:
        if ev.get('event_type') == 'action_confirmed':
            add(_normalise_evidence_items((ev.get('payload') or {}).get('evidence')))

    if evidence:
        return evidence

    for ev in reversed(events):
        if ev.get('event_type') == 'action_proposed':
            add(_normalise_evidence_items((ev.get('payload') or {}).get('evidence')))
            if evidence:
                return evidence
    return []


def _events_to_action_outcome(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for ev in reversed(events):
        payload = ev.get('payload') or {}
        if ev.get('event_type') == 'action_confirmed':
            action_ref = payload.get('action_ref') or payload.get('existing_action_ref')
            return {
                'status': 'created',
                'title': 'Existing action' if payload.get('duplicate') else 'Action created',
                'action_ref': action_ref,
                'table': 'next_actions',
            }
        if ev.get('event_type') == 'action_cancelled':
            return {
                'status': 'cancelled',
                'title': 'Cancelled',
                'message': 'No action created. The proposal was cancelled.',
            }
    return None


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
        SELECT e.payload, e.created_at, t.trace_ref, t.id
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
        cancelled = (await session.execute(text("""
            SELECT 1
            FROM trace_events
            WHERE trace_id = :trace_id
              AND event_type = 'action_cancelled'
              AND payload->>'idempotency_key' = :idempotency_key
            LIMIT 1
        """), {'trace_id': row[3], 'idempotency_key': idem})).first()
        if cancelled is not None:
            return None  # user explicitly cancelled this proposal
    payload.setdefault('trace_ref', row[2])
    return payload


async def db_counts(session: AsyncSession) -> dict[str, int]:
    tables = ['customers', 'issues', 'issue_updates', 'next_actions', 'conversations', 'agent_traces', 'trace_events', 'tool_call_logs', 'rbac_decisions', 'eval_runs', 'eval_results']
    out: dict[str, int] = {}
    for t in tables:
        row = (await session.execute(text(f"SELECT COUNT(*) FROM {t}"))).scalar()
        out[t] = int(row or 0)
    return out
