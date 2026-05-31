"""Decision ledger: collects in-flight trace events for a single request, then
flushes them to PostgreSQL through the repositories module.

The ledger is intentionally in-memory during the request — it's the journal,
not the source of truth. PostgreSQL is the source of truth (see
agent_traces / trace_events tables).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.infrastructure.db import repositories as repo
from acme_app.observability.otel import record_agent_request, record_tool_call


@dataclass
class LedgerEvent:
    event_type: str
    event_name: str
    payload: dict[str, Any]
    status: str = 'ok'
    latency_ms: int | None = None
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class ToolCallRecord:
    tool_name: str
    input_json: dict[str, Any]
    output_summary: dict[str, Any]
    status: str
    latency_ms: int
    error_message: str | None = None


@dataclass
class RbacRecord:
    role: str
    operation: str
    resource: str
    allowed: bool
    reason: str


@dataclass
class Ledger:
    trace_ref: str
    username: str = ''
    user_role: str = ''
    events: list[LedgerEvent] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    rbac_decisions: list[RbacRecord] = field(default_factory=list)
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def event(self, event_type: str, event_name: str, payload: dict[str, Any] | None = None,
              status: str = 'ok', latency_ms: int | None = None) -> None:
        self.events.append(LedgerEvent(event_type, event_name, payload or {}, status, latency_ms))

    def tool(self, tool_name: str, input_json: dict[str, Any], output_summary: dict[str, Any],
             status: str, latency_ms: int, error_message: str | None = None) -> None:
        self.tool_calls.append(ToolCallRecord(tool_name, input_json, output_summary, status, latency_ms, error_message))

    def rbac(self, role: str, operation: str, resource: str, allowed: bool, reason: str) -> None:
        self.rbac_decisions.append(RbacRecord(role, operation, resource, allowed, reason))

    def elapsed_ms(self) -> int:
        return int(time.time() * 1000) - self.started_at_ms


def summarise_output(output: dict[str, Any]) -> dict[str, Any]:
    """Barescope-style summary: shape but not payload."""
    summary: dict[str, Any] = {}
    for key, value in output.items():
        if isinstance(value, list):
            summary[key] = {'kind': 'list', 'length': len(value), 'first_keys': sorted((value[0].keys() if value and isinstance(value[0], dict) else []))}
        elif isinstance(value, dict):
            summary[key] = {'kind': 'dict', 'keys': sorted(value.keys())}
        elif isinstance(value, str):
            summary[key] = value[:120]
        else:
            summary[key] = value
    return summary


async def persist(
    ledger: Ledger,
    session: AsyncSession,
    *,
    conversation_ref: str | None,
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
    otel_trace_id: str | None,
) -> uuid.UUID:
    trace_id = await repo.insert_trace(
        session,
        trace_ref=ledger.trace_ref,
        conversation_ref=conversation_ref,
        username=ledger.username,
        user_role=ledger.user_role,
        user_query=user_query,
        user_query_redacted=user_query_redacted,
        detected_intent=detected_intent,
        final_answer=final_answer,
        final_status=final_status,
        llm_provider=llm_provider,
        llm_model=llm_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost_usd,
        llm_latency_ms=llm_latency_ms,
        tool_latency_ms=tool_latency_ms,
        total_latency_ms=ledger.elapsed_ms(),
        otel_trace_id=otel_trace_id,
    )
    record_agent_request(
        role=ledger.user_role,
        intent=detected_intent,
        status=final_status,
        provider=llm_provider,
        model=llm_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost_usd,
        total_latency_ms=ledger.elapsed_ms(),
        llm_latency_ms=llm_latency_ms,
        tool_latency_ms=tool_latency_ms,
    )
    for event in ledger.events:
        await repo.insert_trace_event(
            session,
            trace_id=trace_id,
            event_type=event.event_type,
            event_name=event.event_name,
            payload=event.payload,
            status=event.status,
            latency_ms=event.latency_ms,
        )
    for call in ledger.tool_calls:
        record_tool_call(
            tool_name=call.tool_name,
            status=call.status,
            latency_ms=call.latency_ms,
        )
        await repo.insert_tool_call_log(
            session,
            trace_id=trace_id,
            tool_name=call.tool_name,
            input_json=call.input_json,
            output_summary=call.output_summary,
            status=call.status,
            latency_ms=call.latency_ms,
            error_message=call.error_message,
        )
    for rbac in ledger.rbac_decisions:
        await repo.insert_rbac_decision(
            session,
            trace_id=trace_id,
            username=ledger.username,
            role=rbac.role,
            operation=rbac.operation,
            resource=rbac.resource,
            allowed=rbac.allowed,
            reason=rbac.reason,
        )
    return trace_id
