from acme_app.application.orchestrator import (
    _clarification_followup_query,
    _external_llm_used,
    _looks_like_confirmation,
    _needs_customer_status_fallback,
    _proposal_denied_answer,
    _render_customer_status_answer,
)


def test_external_llm_used_detects_cloud_plan_or_answer_model():
    assert _external_llm_used('gpt-5.4-mini', 'qwen3.5:9b', None)
    assert _external_llm_used('qwen3.5:9b', 'claude-sonnet-4-6', None)


def test_external_llm_used_detects_cloud_arbiter():
    assert _external_llm_used('qwen3.5:9b', 'qwen3.5:9b', 'arbiter:gpt-5.4-mini')


def test_external_llm_used_is_false_for_local_only():
    assert not _external_llm_used('qwen3.5:9b', 'qwen3.5:9b', 'rules+llama')


def test_short_confirmations_are_deterministic_confirmation_intent():
    assert _looks_like_confirmation('Confirm')
    assert _looks_like_confirmation('yes')
    assert _looks_like_confirmation('go ahead')
    assert not _looks_like_confirmation('Can you confirm the latest status?')


def test_proposal_denied_answer_is_explicit_and_non_staging():
    answer = _proposal_denied_answer(
        'sales_user',
        'PREPARE_RECOVERY_PLAN',
        'sales_user not in allowed_roles for PREPARE_RECOVERY_PLAN',
    )

    assert 'Permission denied' in answer
    assert 'Nothing was staged or created' in answer
    assert 'support user or admin' in answer


def test_customer_status_fallback_detects_under_answered_issue_summary():
    facts = {
        'open_issues': [
            {'issue_ref': 'ISS-401', 'title': 'Manufacturing outage', 'severity': 'P1',
             'status': 'Open', 'sla_status': 'Breached'},
        ],
        'skill_output': {
            'risk_level': 'Critical',
            'recommended_next_action': {'action_type': 'PREPARE_RECOVERY_PLAN', 'priority': 'Critical'},
        },
    }

    assert _needs_customer_status_fallback(
        '### Status Update\n\n- Acme Manufacturing Group · Enterprise · Germany',
        facts,
    )


def test_customer_status_fallback_renders_complete_sales_safe_summary():
    facts = {
        'customer_profile': {'name': 'Acme Manufacturing Group', 'tier': 'Enterprise', 'region': 'Germany'},
        'open_issues': [
            {'issue_ref': 'ISS-401', 'title': 'Manufacturing outage', 'severity': 'P1',
             'status': 'Open', 'sla_status': 'Breached', 'owner': 'Sam Support'},
        ],
        'skill_output': {
            'executive_summary': (
                'Acme Manufacturing Group has 1 open issue(s); highest severity P1, '
                'SLA Breached. Risk: Critical.'
            ),
            'risk_level': 'Critical',
            'risk_factors': ['Enterprise customer', 'P1 issue', 'SLA breached'],
            'recommended_next_action': {
                'action_type': 'PREPARE_RECOVERY_PLAN',
                'priority': 'Critical',
                'rationale': 'Deterministic risk rules; Enterprise customer, P1 issue, SLA breached',
            },
            'missing_information': ['Confirmed engineering resolution date'],
        },
    }

    answer = _render_customer_status_answer(facts, 'sales_user')

    assert 'ISS-401' in answer
    assert 'Manufacturing outage' in answer
    assert 'Risk level: **Critical**' in answer
    assert 'Ask Support to prepare a recovery plan' in answer
    assert 'Confirmed engineering resolution date' in answer


def test_clarification_followup_expands_customer_choice_to_previous_request():
    recent_turns = [
        {'role': 'user', 'text': 'What is going on with Acme?'},
        {'role': 'assistant', 'text': 'Multiple customers match "Acme". Which one did you mean?'},
    ]

    expanded = _clarification_followup_query('Acme Manufacturing Group', recent_turns)

    assert expanded is not None
    assert 'Acme Manufacturing Group' in expanded
    assert 'What is going on with Acme?' in expanded


def test_clarification_followup_preserves_write_request_for_issue_choice():
    recent_turns = [
        {'role': 'user', 'text': 'Create that recovery plan action and assign it to support.'},
        {'role': 'assistant', 'text': 'Which customer or issue should this recovery plan action be associated with? Please provide a customer name or an issue reference.'},
    ]

    expanded = _clarification_followup_query('ISS-102', recent_turns)

    assert expanded is not None
    assert 'ISS-102' in expanded
    assert 'Create that recovery plan action' in expanded
    assert 'write intent' in expanded
