from acme_app.policy.action_catalogue import CATALOGUE, role_allowed, validate_action_type
from acme_app.policy.action_guard import (
    can_propose,
    idempotency_key,
    mint_confirmation_token,
    verify_confirmation_token,
)
from acme_app.policy.rbac import check


def test_sales_user_cannot_create_actions():
    assert check('sales_user', 'create_action').allowed is False
    ok, _ = can_propose('sales_user', 'PREPARE_RECOVERY_PLAN')
    assert ok is False


def test_support_can_propose_most_actions():
    ok, _ = can_propose('support_user', 'PREPARE_RECOVERY_PLAN')
    assert ok is True
    ok, _ = can_propose('support_user', 'CREATE_EXEC_SUMMARY')
    assert ok is False


def test_admin_can_propose_all():
    for action_type in CATALOGUE:
        ok, reason = can_propose('admin', action_type)
        assert ok is True, reason


def test_unknown_role_denied():
    assert check('attacker', 'create_action').allowed is False


def test_unknown_action_type_rejected():
    ok, reason = can_propose('admin', 'BURN_DOWN_DATA_CENTRE')
    assert ok is False
    assert 'Unknown' in reason


def test_action_catalogue_role_allowed():
    assert role_allowed('admin', 'CREATE_EXEC_SUMMARY')
    assert not role_allowed('support_user', 'CREATE_EXEC_SUMMARY')
    assert not role_allowed('sales_user', 'PREPARE_RECOVERY_PLAN')


def test_validate_action_type_only_eight():
    assert validate_action_type('PREPARE_RECOVERY_PLAN')
    assert not validate_action_type('NOT_REAL')


def test_idempotency_key_stable_for_same_inputs():
    a = idempotency_key('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    b = idempotency_key('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    c = idempotency_key('TRC-2', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    assert a == b
    assert a != c


def test_confirmation_token_roundtrip():
    token = mint_confirmation_token('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    ok, _reason, parts = verify_confirmation_token(token)
    assert ok is True
    assert parts['action_type'] == 'PREPARE_RECOVERY_PLAN'


def test_confirmation_token_tampered_rejected():
    token = mint_confirmation_token('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    tampered = token[:-4] + 'aaaa'
    ok, reason, _ = verify_confirmation_token(tampered)
    assert ok is False
    assert 'signature' in reason
