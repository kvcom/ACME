from acme_app.skills import closure_readiness_check, customer_escalation_summary
from acme_app.skills.registry import SKILLS, VERSIONS


NORTHWIND = {'customer_id': 'CUST-001', 'name': 'Northwind Energy', 'tier': 'Enterprise', 'region': 'UK'}


def test_escalation_summary_critical_path():
    issues = [{'issue_ref': 'ISS-102', 'severity': 'P1', 'sla_status': 'Breached', 'status': 'Open', 'owner': 'Sam'}]
    updates = [{'update_text': 'Customer asked for recovery plan.', 'update_type': 'customer_update', 'id': 'U1'}]
    out = customer_escalation_summary.run(NORTHWIND, issues, updates, 'sales_user')
    assert out['risk_level'] == 'Critical'
    assert out['recommended_next_action']['action_type'] == 'PREPARE_RECOVERY_PLAN'
    assert out['recommended_next_action']['priority'] == 'Critical'
    assert any(ev.startswith('issue:ISS-102') for ev in out['evidence'])


def test_escalation_summary_no_open_issues():
    out = customer_escalation_summary.run(NORTHWIND, [], [], 'sales_user')
    assert out['risk_level'] == 'Low'
    assert out['recommended_next_action']['action_type'] == 'SCHEDULE_REVIEW'


def test_escalation_summary_missing_customer():
    out = customer_escalation_summary.run({}, [], [], 'sales_user')
    assert out['risk_level'] == 'Low'
    assert 'Customer identity' in out['missing_information']


def test_closure_readiness_not_ready_without_acceptance():
    issue = {'issue_ref': 'ISS-102', 'status': 'Open'}
    updates = [{'update_text': 'Engineering still investigating', 'update_type': 'engineering_update', 'id': 'U1'}]
    out = closure_readiness_check.run('ISS-102', issue, updates)
    assert out['ready_to_close'] is False
    assert 'Customer acceptance' in out['missing_information']
    assert out['recommended_next_action']['action_type'] == 'REQUEST_MISSING_INFO'


def test_closure_readiness_ready_when_resolved_and_accepted():
    issue = {'issue_ref': 'ISS-900', 'status': 'Resolved'}
    updates = [
        {'update_text': 'Engineering confirmed resolution', 'update_type': 'engineering_update', 'id': 'U1'},
        {'update_text': 'Customer acceptance recorded', 'update_type': 'customer_update', 'id': 'U2'},
    ]
    out = closure_readiness_check.run('ISS-900', issue, updates)
    assert out['ready_to_close'] is True


def test_skills_registry_has_both():
    assert 'customer_escalation_summary' in SKILLS
    assert 'closure_readiness_check' in SKILLS
    assert VERSIONS['customer_escalation_summary'] == 'v1'
