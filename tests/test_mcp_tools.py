import pytest

from acme_mcp import validation
from acme_mcp.schemas import CreateActionInput, IssueRefInput, SearchCustomersInput


def test_search_customers_accepts_empty():
    SearchCustomersInput.model_validate({'customer_name': ''})


def test_issue_ref_pattern_enforced():
    with pytest.raises(Exception):
        IssueRefInput.model_validate({'issue_ref': 'random text'})
    IssueRefInput.model_validate({'issue_ref': 'ISS-102'})


def test_create_action_schema_validates():
    payload = {
        'actor': {'username': 'sam.support', 'role': 'support_user'},
        'issue_ref': 'ISS-102',
        'action_type': 'PREPARE_RECOVERY_PLAN',
        'title': 'Recovery plan',
        'description': 'Plan',
        'priority': 'High',
        'evidence': ['issue:ISS-102'],
        'idempotency_key': 'a' * 32,
        'confirmation_token': 'a' * 32,
    }
    CreateActionInput.model_validate(payload)


def test_role_write_permissions():
    assert not validation.role_may_create('sales_user', 'PREPARE_RECOVERY_PLAN')
    assert validation.role_may_create('support_user', 'PREPARE_RECOVERY_PLAN')
    assert validation.role_may_create('admin', 'CREATE_EXEC_SUMMARY')
    assert not validation.role_may_create('support_user', 'CREATE_EXEC_SUMMARY')


def _mint(trace_ref, action_type, resource_ref, *, secret=None, expires_in_s=600):
    """Mint a confirmation token the way the app does, for verify-side tests."""
    import hashlib
    import hmac
    import time

    from acme_mcp import tools as mcp_tools

    secret = secret or mcp_tools.HMAC_SECRET
    expires_at = int(time.time()) + expires_in_s
    payload = f'{trace_ref}|{action_type}|{resource_ref}|{expires_at}'
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f'{payload}|{sig}'


def test_verify_token_binds_to_resource_ref():
    """A token minted for one action_ref must not verify against another —
    the replay-binding fix for update_next_action (audit Robustness #6)."""
    from acme_mcp import tools as mcp_tools

    token = _mint('TRC-1', 'UPDATE_NEXT_ACTION', 'ACT-7')

    ok, _ = mcp_tools._verify_token(
        token, expected_action_type='UPDATE_NEXT_ACTION', expected_issue_ref='ACT-7')
    assert ok

    # Same token replayed against a different action is rejected.
    bad, reason = mcp_tools._verify_token(
        token, expected_action_type='UPDATE_NEXT_ACTION', expected_issue_ref='ACT-99')
    assert not bad
    assert 'mismatch' in reason


def test_verify_token_binds_to_action_type():
    from acme_mcp import tools as mcp_tools

    token = _mint('TRC-1', 'UPDATE_ISSUE_STATUS', 'ISS-102')
    ok, _ = mcp_tools._verify_token(
        token, expected_action_type='UPDATE_ISSUE_STATUS', expected_issue_ref='ISS-102')
    assert ok
    bad, reason = mcp_tools._verify_token(
        token, expected_action_type='UPDATE_NEXT_ACTION', expected_issue_ref='ISS-102')
    assert not bad
    assert 'action_type mismatch' in reason


def test_verify_token_rejects_tampered_signature():
    from acme_mcp import tools as mcp_tools

    token = _mint('TRC-1', 'UPDATE_ISSUE_STATUS', 'ISS-102')
    tampered = token.rsplit('|', 1)[0] + '|' + ('0' * 64)
    ok, reason = mcp_tools._verify_token(tampered)
    assert not ok
    assert reason == 'signature mismatch'


@pytest.mark.integration
def test_search_customers_against_db():
    from acme_mcp import tools
    out = tools.search_customers('Northwind')
    assert any('Northwind' in m['name'] for m in out['matches'])
