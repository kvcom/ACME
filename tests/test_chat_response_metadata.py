from acme_app.application.orchestrator import (
    _clarification_followup_query,
    _comparison_customers,
    _external_llm_used,
    _ingest_tool_output,
    _invoke_skill,
    _looks_like_confirmation,
    _needs_customer_comparison_fallback,
    _needs_customer_status_fallback,
    _proposal_denied_answer,
    _recent_customer_scope,
    _render_customer_comparison_answer,
    _render_customer_status_answer,
)
from acme_app.application.outbound_privacy import build_privacy_context


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


def test_customer_comparison_fallback_detects_missing_customer_and_renders_both():
    facts = {
        'comparison_customers': [
            {
                'customer_name': 'Northwind Energy',
                'profile': {'name': 'Northwind Energy', 'tier': 'Enterprise', 'region': 'UK'},
                'open_issues': [
                    {
                        'issue_ref': 'ISS-102',
                        'title': 'API integration delay',
                        'severity': 'P1',
                        'status': 'Open',
                        'sla_status': 'Breached',
                    },
                ],
                'risk_level': 'Critical',
                'recommended_next_action': {
                    'action_type': 'PREPARE_RECOVERY_PLAN',
                    'priority': 'Critical',
                    'rationale': 'Enterprise customer, P1 issue, SLA breached',
                },
            },
            {
                'customer_name': 'Skyline Aviation',
                'profile': {'name': 'Skyline Aviation', 'tier': 'Enterprise', 'region': 'France'},
                'open_issues': [
                    {
                        'issue_ref': 'ISS-501',
                        'title': 'Maintenance scheduling drift',
                        'severity': 'P2',
                        'status': 'Open',
                        'sla_status': 'At Risk',
                    },
                ],
                'risk_level': 'Low',
                'recommended_next_action': {
                    'action_type': 'SCHEDULE_REVIEW',
                    'priority': 'Low',
                    'rationale': 'Enterprise customer, P2 issue, SLA at risk',
                },
            },
        ],
    }

    assert _needs_customer_comparison_fallback(
        '### Status Update for Skyline Aviation\n\nISS-501 is at risk.',
        facts['comparison_customers'],
    )

    answer = _render_customer_comparison_answer(facts, 'support_user')

    assert 'Northwind Energy' in answer
    assert 'Skyline Aviation' in answer
    assert '**Northwind Energy** more urgently needs action.' in answer
    assert 'ISS-102' in answer
    assert 'ISS-501' in answer
    assert 'Prepare Recovery Plan' in answer
    assert 'Schedule Review' in answer


def test_recent_customer_scope_prefers_user_comparison_over_assistant_drift():
    privacy = build_privacy_context(
        model_key_or_provider='gpt-5.5',
        customers=[
            {'customer_id': 'C1', 'name': 'Acme Logistics Europe'},
            {'customer_id': 'C2', 'name': 'Acme Manufacturing Group'},
            {'customer_id': 'C3', 'name': 'Contoso Retail'},
            {'customer_id': 'C4', 'name': 'Skyline Aviation'},
        ],
        users=[],
        pii_substrings=[],
    )
    turns = [
        {
            'role': 'user',
            'text': 'Acme Logistics Europe vs Contoso Retail vs Skyline Aviation - compare them',
        },
        {
            'role': 'assistant',
            'text': 'Acme Manufacturing Group has the most pressing issue.',
        },
    ]

    assert _recent_customer_scope(turns, privacy) == [
        'Acme Logistics Europe',
        'Contoso Retail',
        'Skyline Aviation',
    ]


async def test_multi_customer_facts_do_not_overwrite_each_other(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        'acme_app.application.orchestrator.conversation_memory.set_last_customer',
        _noop,
    )
    monkeypatch.setattr(
        'acme_app.application.orchestrator.conversation_memory.set_last_issue',
        _noop,
    )

    facts = {}
    evidence = []

    await _ingest_tool_output(
        'get_customer_profile',
        {'customer_name': 'Northwind Energy'},
        {'customer_id': 'C-N', 'name': 'Northwind Energy', 'tier': 'Enterprise', 'region': 'UK'},
        facts,
        evidence,
        'sam.support',
        'CONV-TEST',
    )
    await _ingest_tool_output(
        'get_open_issues',
        {'customer_name': 'Northwind Energy'},
        {'customer_id': 'C-N', 'issues': [
            {'issue_ref': 'ISS-102', 'severity': 'P1', 'sla_status': 'Breached', 'status': 'Open', 'owner': 'Sam'},
        ]},
        facts,
        evidence,
        'sam.support',
        'CONV-TEST',
    )
    await _ingest_tool_output(
        'get_customer_profile',
        {'customer_name': 'Skyline Aviation'},
        {'customer_id': 'C-S', 'name': 'Skyline Aviation', 'tier': 'Enterprise', 'region': 'France'},
        facts,
        evidence,
        'sam.support',
        'CONV-TEST',
    )
    await _ingest_tool_output(
        'get_open_issues',
        {'customer_name': 'Skyline Aviation'},
        {'customer_id': 'C-S', 'issues': [
            {'issue_ref': 'ISS-501', 'severity': 'P2', 'sla_status': 'At Risk', 'status': 'Open', 'owner': 'Sam'},
        ]},
        facts,
        evidence,
        'sam.support',
        'CONV-TEST',
    )

    northwind = _invoke_skill(
        'customer_escalation_summary',
        {'customer_name': 'Northwind Energy'},
        facts,
        'support_user',
    )
    skyline = _invoke_skill(
        'customer_escalation_summary',
        {'customer_name': 'Skyline Aviation'},
        facts,
        'support_user',
    )

    assert northwind['risk_level'] == 'Critical'
    assert skyline['risk_level'] == 'Low'
    assert 'Northwind Energy' in northwind['executive_summary']
    assert 'Skyline Aviation' in skyline['executive_summary']

    facts['customer_facts']['Northwind Energy']['skill_output'] = northwind
    facts['customer_facts']['Skyline Aviation']['skill_output'] = skyline
    comparison = _comparison_customers(facts)
    assert [row['customer_name'] for row in comparison] == [
        'Northwind Energy',
        'Skyline Aviation',
    ]
    assert comparison[0]['risk_level'] == 'Critical'
    assert comparison[1]['risk_level'] == 'Low'


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
