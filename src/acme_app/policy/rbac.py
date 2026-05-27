"""Role-based access control.

The matrix is intentionally explicit. RBAC is enforced server-side based on the
Keycloak token, never on what the LLM claims about the user's role.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RbacDecision:
    allowed: bool
    reason: str


READ_OPS = {'read_customer', 'read_issue', 'read_action', 'recommend_action'}
WRITE_OPS = {'create_action', 'update_action', 'update_issue_status', 'cancel_action'}

MATRIX: dict[str, dict[str, bool]] = {
    'sales_user': {
        'read_customer': True,
        'read_issue': True,
        'read_action': True,
        'recommend_action': True,
        'create_action': False,
        'update_action': False,
        'update_issue_status': False,
        'cancel_action': False,
    },
    'support_user': {
        'read_customer': True,
        'read_issue': True,
        'read_action': True,
        'recommend_action': True,
        'create_action': True,
        'update_action': True,
        'update_issue_status': True,
        'cancel_action': False,
    },
    'admin': {
        'read_customer': True,
        'read_issue': True,
        'read_action': True,
        'recommend_action': True,
        'create_action': True,
        'update_action': True,
        'update_issue_status': True,
        'cancel_action': True,
    },
}


def check(role: str, operation: str) -> RbacDecision:
    permissions = MATRIX.get(role)
    if permissions is None:
        return RbacDecision(allowed=False, reason=f'Unknown role: {role}')
    allowed = permissions.get(operation, False)
    if allowed:
        return RbacDecision(allowed=True, reason=f'{role} may {operation}')
    return RbacDecision(allowed=False, reason=f'{role} cannot {operation}')


def roles_for(operation: str) -> list[str]:
    return [role for role, perms in MATRIX.items() if perms.get(operation)]
