ALLOWED_ACTION_TYPES = {
    'ASSIGN_OWNER',
    'REQUEST_MISSING_INFO',
    'CUSTOMER_FOLLOW_UP',
    'ESCALATE_ISSUE',
    'PREPARE_RECOVERY_PLAN',
    'SCHEDULE_REVIEW',
    'UPDATE_ISSUE_STATUS',
    'CREATE_EXEC_SUMMARY',
}


def validate_action_type(action_type: str) -> bool:
    return action_type in ALLOWED_ACTION_TYPES
