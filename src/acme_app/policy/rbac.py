from dataclasses import dataclass


@dataclass
class RbacDecision:
    allowed: bool
    reason: str


MATRIX = {
    'sales_user': {'read_customer': True, 'read_issue': True, 'create_action': False, 'update_issue_status': False, 'update_next_action': False},
    'support_user': {'read_customer': True, 'read_issue': True, 'create_action': True, 'update_issue_status': True, 'update_next_action': True},
    'admin': {'read_customer': True, 'read_issue': True, 'create_action': True, 'update_issue_status': True, 'update_next_action': True},
}


def check(role: str, operation: str) -> RbacDecision:
    allowed = MATRIX.get(role, {}).get(operation, False)
    return RbacDecision(allowed, 'Allowed by RBAC policy' if allowed else f'{role} cannot perform {operation}')
