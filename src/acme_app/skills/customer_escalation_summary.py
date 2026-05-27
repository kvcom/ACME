from acme_app.domain.risk_rules import classify_risk


def run(customer: dict, issues: list[dict], updates: list[dict]) -> dict:
    severity_order = {'P1': 1, 'P2': 2, 'P3': 3, 'P4': 4}
    highest = 'P4'
    sla = 'Within SLA'
    if issues:
        highest = sorted(issues, key=lambda x: severity_order.get(x.get('severity', 'P4'), 4))[0].get('severity', 'P4')
        sla = issues[0].get('sla_status', 'Within SLA')
    risk = classify_risk(customer.get('tier', 'Mid-market'), highest, sla, max(0, len(updates)-1))
    action_type = 'PREPARE_RECOVERY_PLAN' if risk in {'High', 'Critical'} else 'SCHEDULE_REVIEW'
    return {
        'executive_summary': f"{customer.get('name', 'Customer')} has {len(issues)} open issues.",
        'risk_level': risk,
        'recommended_next_action': {
            'action_type': action_type,
            'priority': 'High' if risk in {'High', 'Critical'} else 'Medium',
            'title': f"{action_type.replace('_', ' ').title()} for {customer.get('name', 'customer')}",
            'rationale': f'Deterministic risk rules assessed {risk}',
        },
        'missing_information': ['Latest customer sentiment', 'Confirmed resolution date'],
        'evidence': [i.get('issue_ref', '') for i in issues],
    }
