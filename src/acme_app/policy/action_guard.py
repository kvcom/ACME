from acme_app.policy.action_catalogue import validate_action_type
from acme_app.policy.rbac import check


def can_propose(role: str, action_type: str) -> tuple[bool, str]:
    if not validate_action_type(action_type):
        return False, 'Unknown action type'
    decision = check(role, 'create_action')
    return decision.allowed, decision.reason
