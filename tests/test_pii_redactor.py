from acme_app.policy.pii_redactor import has_pii, redact


def test_email_redacted():
    assert redact('contact me at sam@example.com') == 'contact me at [REDACTED-EMAIL]'


def test_card_redacted():
    text = 'card 4111111111111111 expires soon'
    out = redact(text)
    assert '[REDACTED-CARD]' in out
    assert '4111' not in out


def test_id_redacted():
    out = redact('employee 123456789 left')
    assert '[REDACTED-ID]' in out


def test_clean_text_unchanged():
    assert redact('Acme has an open issue with ISS-102.') == 'Acme has an open issue with ISS-102.'


def test_has_pii_flags():
    assert has_pii('reach me at user@example.com')
    assert not has_pii('no pii here at all')


def test_multiple_pii_handled():
    out = redact('email user@example.com card 4111111111111111')
    assert '[REDACTED-EMAIL]' in out
    assert '[REDACTED-CARD]' in out
