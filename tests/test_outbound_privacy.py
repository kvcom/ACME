from types import SimpleNamespace

from acme_app.application.outbound_privacy import (
    build_privacy_context,
    privacy_manifest,
    restore_text_from_llm,
    sanitize_facts_for_llm,
    sanitize_text_for_llm,
    sanitize_text_with_report,
    translate_customer_args_to_names,
)


CUSTOMERS = [
    {
        'customer_id': '01fab16e-116b-4be5-935e-15cfca000001',
        'name': 'Acme Logistics Europe',
        'tier': 'Enterprise',
        'region': 'Netherlands',
    },
]
USERS = [
    {
        'user_id': '9c5214ac-6e7c-41b7-9c72-000000000001',
        'username': 'sam.support',
        'email': 'sam.support@example.local',
        'display_name': 'Sam Support',
    },
]


def test_external_prompt_replaces_customer_name_with_db_id_and_redacts_pii():
    privacy = build_privacy_context(
        model_key_or_provider='claude-opus-4-8',
        customers=CUSTOMERS,
        users=USERS,
        pii_substrings=['Jane Smith'],
    )

    outbound = sanitize_text_for_llm(
        'Ask Jane Smith about Acme Logistics Europe and Sam Support at jane@example.com or +1 415 555 1212.',
        privacy,
    )

    assert 'Acme Logistics Europe' not in outbound
    assert 'Sam Support' not in outbound
    assert 'customers.id=01fab16e-116b-4be5-935e-15cfca000001' in outbound
    assert 'users.id=9c5214ac-6e7c-41b7-9c72-000000000001' in outbound
    assert 'Jane Smith' not in outbound
    assert '[REDACTED-LLM]' in outbound
    assert '[REDACTED-EMAIL]' in outbound
    assert '[REDACTED-PHONE]' in outbound


def test_privacy_manifest_only_lists_applied_replacements_by_default():
    privacy = build_privacy_context(
        model_key_or_provider='claude-opus-4-8',
        customers=CUSTOMERS + [{
            'customer_id': '02fab16e-116b-4be5-935e-15cfca000002',
            'name': 'Unused Customer',
        }],
        users=USERS,
        pii_substrings=[],
    )
    applied = sanitize_text_with_report('Status for Acme Logistics Europe', privacy)
    manifest = privacy_manifest(privacy, applied=applied)

    assert [r['internal'] for r in manifest['customer_replacements']] == ['Acme Logistics Europe']
    assert manifest['user_replacements'] == []
    assert manifest['available_dictionary_counts']['customers'] == 2


def test_external_facts_replace_customer_names_and_person_fields():
    privacy = build_privacy_context(
        model_key_or_provider='gpt-5.5',
        customers=CUSTOMERS,
        users=USERS,
        pii_substrings=[],
    )
    facts = {
        'customer_profile': {
            'customer_id': '01fab16e-116b-4be5-935e-15cfca000001',
            'name': 'Acme Logistics Europe',
            'account_owner': 'Sam Support',
        },
        'open_issues': [
            {'issue_ref': 'ISS-900', 'title': 'Integration delay', 'owner': 'Sam Support'},
        ],
    }

    outbound = sanitize_facts_for_llm(facts, privacy)

    assert outbound['customer_profile']['name'] == 'customers.id=01fab16e-116b-4be5-935e-15cfca000001'
    assert outbound['customer_profile']['account_owner'] == 'users.id=9c5214ac-6e7c-41b7-9c72-000000000001'
    assert outbound['open_issues'][0]['owner'] == 'users.id=9c5214ac-6e7c-41b7-9c72-000000000001'


def test_customer_id_plan_args_and_answers_translate_back_to_names():
    privacy = build_privacy_context(
        model_key_or_provider='claude-opus-4-8',
        customers=CUSTOMERS,
        users=USERS,
        pii_substrings=[],
    )
    plan = SimpleNamespace(steps=[
        SimpleNamespace(arguments={'customer_name': 'customers.id=01fab16e-116b-4be5-935e-15cfca000001'}),
    ])

    translate_customer_args_to_names(plan, privacy)
    answer = restore_text_from_llm(
        'customers.id=01fab16e-116b-4be5-935e-15cfca000001 needs action by users.id=9c5214ac-6e7c-41b7-9c72-000000000001.',
        privacy,
    )

    assert plan.steps[0].arguments['customer_name'] == 'Acme Logistics Europe'
    assert answer == 'Acme Logistics Europe needs action by Sam Support.'


def test_ambiguous_customer_first_token_is_not_used_as_alias():
    privacy = build_privacy_context(
        model_key_or_provider='claude-opus-4-8',
        customers=CUSTOMERS + [{
            'customer_id': '02fab16e-116b-4be5-935e-15cfca000002',
            'name': 'Acme Manufacturing Group',
        }],
        users=[],
        pii_substrings=[],
    )

    exact = sanitize_text_with_report('Acme Logistics Europe vs Contoso', privacy)
    ambiguous = sanitize_text_with_report('What is going on with Acme?', privacy)

    assert 'customers.id=01fab16e-116b-4be5-935e-15cfca000001' in exact.text
    assert 'customers.id=02fab16e-116b-4be5-935e-15cfca000002' not in exact.text
    assert ambiguous.text == 'What is going on with Acme?'
    assert ambiguous.customer_replacements == ()


def test_restore_scrubs_internal_token_instruction_leaks():
    privacy = build_privacy_context(
        model_key_or_provider='gpt-5.5',
        customers=CUSTOMERS,
        users=USERS,
        pii_substrings=[],
    )

    answer = restore_text_from_llm(
        'Send the three customers.id=<uuid> values, or use customers.id=deadbeef-dead-beef-dead-beefdeadbeef.',
        privacy,
    )

    assert 'customers.id' not in answer
    assert '<uuid>' not in answer
    assert 'deadbeef' not in answer
