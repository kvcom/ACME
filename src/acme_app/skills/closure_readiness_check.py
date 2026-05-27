def run(issue_ref: str, has_customer_acceptance: bool, blockers_open: bool) -> dict:
    ready = has_customer_acceptance and not blockers_open
    return {
        'ready_to_close': ready,
        'reason': 'Ready' if ready else 'No customer acceptance note and/or blockers still open',
        'missing_information': [] if ready else ['Customer acceptance', 'Technical resolution confirmation'],
        'recommended_next_action': {
            'action_type': 'REQUEST_MISSING_INFO' if not ready else 'UPDATE_ISSUE_STATUS',
            'priority': 'High' if not ready else 'Medium',
        },
        'issue_ref': issue_ref,
    }
