"""Input sanitisation shared by MCP tools."""
from __future__ import annotations

ALLOWED_ACTION_TYPES = frozenset({
    'ASSIGN_OWNER', 'REQUEST_MISSING_INFO', 'CUSTOMER_FOLLOW_UP', 'ESCALATE_ISSUE',
    'PREPARE_RECOVERY_PLAN', 'SCHEDULE_REVIEW', 'UPDATE_ISSUE_STATUS', 'CREATE_EXEC_SUMMARY',
})

ALLOWED_PRIORITIES = frozenset({'Low', 'Medium', 'High', 'Critical'})

ALLOWED_ACTION_STATUSES = frozenset({'Proposed', 'Open', 'In Progress', 'Blocked', 'Completed', 'Cancelled'})

ALLOWED_ISSUE_STATUSES = frozenset({'Open', 'In Progress', 'Waiting for Customer', 'Escalated', 'Resolved', 'Closed'})

ROLE_WRITE_PERMISSIONS: dict[str, frozenset[str]] = {
    'sales_user': frozenset(),
    'support_user': frozenset({'ASSIGN_OWNER', 'REQUEST_MISSING_INFO', 'CUSTOMER_FOLLOW_UP', 'ESCALATE_ISSUE',
                               'PREPARE_RECOVERY_PLAN', 'SCHEDULE_REVIEW', 'UPDATE_ISSUE_STATUS'}),
    'admin': ALLOWED_ACTION_TYPES,
}


def role_may_create(role: str, action_type: str) -> bool:
    return action_type in ROLE_WRITE_PERMISSIONS.get(role, frozenset())
