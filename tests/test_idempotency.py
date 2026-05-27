from acme_app.application.propose_confirm import build_idempotency_key


def test_idempotency_is_stable() -> None:
    a = build_idempotency_key('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    b = build_idempotency_key('TRC-1', 'PREPARE_RECOVERY_PLAN', 'ISS-102')
    assert a == b
