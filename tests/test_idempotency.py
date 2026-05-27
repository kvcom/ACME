from acme_app.policy.action_guard import idempotency_key, mint_confirmation_token, verify_confirmation_token


def test_idempotency_key_is_sha256_hex():
    key = idempotency_key('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    assert len(key) == 64
    int(key, 16)


def test_idempotency_key_differs_per_issue():
    a = idempotency_key('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    b = idempotency_key('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-301')
    assert a != b


def test_confirmation_token_format():
    token = mint_confirmation_token('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    parts = token.split('|')
    assert len(parts) == 5


def test_confirmation_token_action_type_binding():
    token = mint_confirmation_token('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    ok, _, parts = verify_confirmation_token(token)
    assert ok
    assert parts['action_type'] == 'PREPARE_RECOVERY_PLAN'
    assert parts['issue_ref'] == 'ISS-102'
