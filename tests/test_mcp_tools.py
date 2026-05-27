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


@pytest.mark.integration
def test_search_customers_against_db():
    from acme_mcp import tools
    out = tools.search_customers('Northwind')
    assert any('Northwind' in m['name'] for m in out['matches'])
