from acme_app.application.orchestrator import _explicit_write_intent


def test_briefing_request_is_not_write_intent():
    assert not _explicit_write_intent(
        'I have a call with Northwind today. What are the open issues, '
        'latest status, and recommended next step?'
    )


def test_explicit_action_request_is_write_intent():
    assert _explicit_write_intent(
        'For Northwind issue ISS-102, prepare a high-priority recovery plan action.'
    )


def test_create_request_is_write_intent():
    assert _explicit_write_intent('Create that recovery plan action and assign it to support.')


def test_open_issues_is_not_write_intent_but_open_ticket_is():
    assert not _explicit_write_intent('Show me the open issues for Northwind.')
    assert _explicit_write_intent('Open a ticket for ISS-102.')
