import pytest

from acme_app.application.propose_confirm import (
    build_proposed_action,
    clear_pending_action,
    get_pending_action,
    stage_pending_action,
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
