from acme_app.skills.customer_escalation_summary import run


def test_skill_recommends_action() -> None:
    out = run({'name': 'Northwind', 'tier': 'Enterprise'}, [{'issue_ref': 'ISS-102', 'severity': 'P1', 'sla_status': 'Breached'}], [])
    assert out['recommended_next_action']['action_type'] == 'PREPARE_RECOVERY_PLAN'
