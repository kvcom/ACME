import pytest

from acme_app.application.propose_confirm import (
    build_proposed_action,
    clear_pending_action,
    confirm_payload,
    get_pending_action,
    stage_pending_action,
    target_tool_for,
)


@pytest.mark.asyncio
async def test_propose_then_get_then_clear():
    payload = build_proposed_action(
        trace_ref='TRC-1', customer_id='C1', customer_name='Northwind Energy',
        issue_ref='ISS-102',
        recommendation={
            'action_type': 'PREPARE_RECOVERY_PLAN', 'priority': 'High',
            'title': 'Recovery plan', 'description': 'Plan',
            'rationale': 'P1 + SLA breached', 'evidence': ['issue:ISS-102'],
        },
    )
    await stage_pending_action('TEST-CONV', payload)
    pending = await get_pending_action('TEST-CONV')
    assert pending is not None
    assert pending['action_type'] == 'PREPARE_RECOVERY_PLAN'
    assert pending['confirmation_token'].count('|') == 4
    await clear_pending_action('TEST-CONV')
    after = await get_pending_action('TEST-CONV')
    assert after is None


def test_build_proposed_action_includes_token_and_key():
    out = build_proposed_action(
        trace_ref='TRC-X', customer_id='C', customer_name='N',
        issue_ref='ISS-102',
        recommendation={'action_type': 'ESCALATE_ISSUE', 'priority': 'High',
                        'title': 't', 'description': 'd', 'rationale': 'r', 'evidence': []},
    )
    assert out['confirmation_token']
    assert len(out['idempotency_key']) == 64


# ── Generalised target-tool routing (audit Requirement Gap #2) ──────────────


def test_target_tool_routing():
    assert target_tool_for('UPDATE_ISSUE_STATUS') == 'update_issue_status'
    assert target_tool_for('UPDATE_NEXT_ACTION') == 'update_next_action'
    assert target_tool_for('PREPARE_RECOVERY_PLAN') == 'create_next_action'
    assert target_tool_for('ANYTHING_ELSE') == 'create_next_action'


def test_confirm_payload_dispatches_create():
    pending = build_proposed_action(
        trace_ref='TRC-C', customer_id='C', customer_name='N', issue_ref='ISS-102',
        recommendation={'action_type': 'PREPARE_RECOVERY_PLAN', 'priority': 'High',
                        'title': 't', 'description': 'd', 'rationale': 'r', 'evidence': []},
    )
    tool, payload = confirm_payload(pending, {'username': 'sam.support', 'role': 'support_user'})
    assert tool == 'create_next_action'
    assert payload['issue_ref'] == 'ISS-102'
    assert payload['action_type'] == 'PREPARE_RECOVERY_PLAN'
    assert payload['idempotency_key'] == pending['idempotency_key']
    assert payload['confirmation_token'] == pending['confirmation_token']


def test_confirm_payload_dispatches_issue_status_update():
    pending = build_proposed_action(
        trace_ref='TRC-U', customer_id='C', customer_name='N', issue_ref='ISS-102',
        recommendation={'action_type': 'UPDATE_ISSUE_STATUS', 'priority': 'Medium',
                        'title': 'Close ISS-102', 'new_status': 'Resolved',
                        'rationale': 'done', 'evidence': []},
    )
    assert pending['target_tool'] == 'update_issue_status'
    tool, payload = confirm_payload(pending, {'username': 'sam.support', 'role': 'support_user'})
    assert tool == 'update_issue_status'
    assert payload['issue_ref'] == 'ISS-102'
    assert payload['new_status'] == 'Resolved'
    # The token is bound to the issue_ref for issue-status updates.
    assert payload['confirmation_token'].split('|')[2] == 'ISS-102'


def test_confirm_payload_dispatches_next_action_update_and_binds_token():
    pending = build_proposed_action(
        trace_ref='TRC-N', customer_id='C', customer_name='N', issue_ref='ISS-102',
        recommendation={'action_type': 'UPDATE_NEXT_ACTION', 'priority': 'Medium',
                        'title': 'Complete', 'new_status': 'Completed',
                        'action_ref': 'ACT-7', 'rationale': 'done', 'evidence': []},
    )
    assert pending['target_tool'] == 'update_next_action'
    tool, payload = confirm_payload(pending, {'username': 'admin.acme', 'role': 'admin'})
    assert tool == 'update_next_action'
    assert payload['action_ref'] == 'ACT-7'
    assert payload['new_status'] == 'Completed'
    # The token's resource slot must carry the action_ref (not the issue_ref)
    # so it cannot be replayed against a different action.
    assert payload['confirmation_token'].split('|')[2] == 'ACT-7'
