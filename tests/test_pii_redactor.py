from acme_app.policy.pii_redactor import redact


def test_redacts_email() -> None:
    assert '[REDACTED-EMAIL]' in redact('user@example.com')
