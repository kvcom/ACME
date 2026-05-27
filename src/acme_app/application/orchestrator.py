import time
import uuid

from acme_app.application.adversarial import check_query
from acme_app.application.planner import create_plan
from acme_app.application.propose_confirm import stage_pending_action
from acme_app.infrastructure.mcp_client.client import MCPClient
from acme_app.observability.cost_calculator import compute
from acme_app.observability.decision_ledger import add_event
from acme_app.policy.action_guard import can_propose
from acme_app.policy.pii_redactor import redact


async def run_agent(query: str, provider_name: str, role: str, conversation_ref: str) -> dict:
    trace_ref = f"TRC-{str(uuid.uuid4())[:8].upper()}"
    start_ms = int(time.time() * 1000)

    ok, flags = check_query(query)
    add_event(trace_ref, 'adversarial', 'adversarial.check', {'flags': flags}, status='ok' if ok else 'blocked')
    if not ok:
        return {'trace_ref': trace_ref, 'badge': 'Adversarial Input Blocked', 'answer': 'Input too long.', 'events': [], 'cost_usd': 0.0, 'total_tokens': 0, 'latency_ms': int(time.time() * 1000) - start_ms}

    plan = await create_plan(query, provider_name)
    add_event(trace_ref, 'agent_plan', 'plan.created', plan.model_dump())

    mcp = MCPClient()
    tool_results = []
    for step in plan.steps:
        if step.step_type != 'tool':
            continue
        add_event(trace_ref, 'tool_call', f"tool.{step.name}.start", step.arguments)
        try:
            result = await mcp.call_tool(step.name, step.arguments)
            add_event(trace_ref, 'tool_call', f"tool.{step.name}.complete", {'keys': list(result.keys())})
            tool_results.append({'tool': step.name, 'result': result})
        except Exception as exc:
            add_event(trace_ref, 'error', f"tool.{step.name}.error", {'error': str(exc)}, status='error')

    proposed_action = None
    if plan.write_requested:
        allowed, reason = can_propose(role, 'PREPARE_RECOVERY_PLAN')
        add_event(trace_ref, 'rbac_decision', 'rbac.create_action', {'allowed': allowed, 'reason': reason})
        if allowed:
            proposed_action = await stage_pending_action(conversation_ref, {'trace_ref': trace_ref, 'action_type': 'PREPARE_RECOVERY_PLAN', 'issue_ref': 'ISS-102', 'priority': 'High', 'title': 'Prepare recovery plan for ISS-102'})
            add_event(trace_ref, 'action_proposed', 'action.proposed', proposed_action)

    prompt_tokens = max(100, len(query) // 2)
    completion_tokens = 220
    return {
        'trace_ref': trace_ref,
        'badge': 'Action Proposed' if proposed_action else 'Grounded',
        'answer': f"Processed query with {len(tool_results)} tool call(s)." + (' Action has been proposed for confirmation.' if proposed_action else ''),
        'events': tool_results,
        'proposed_action': proposed_action,
        'cost_usd': compute(provider_name, prompt_tokens, completion_tokens),
        'total_tokens': prompt_tokens + completion_tokens,
        'latency_ms': int(time.time() * 1000) - start_ms,
        'query_redacted': redact(query),
    }
