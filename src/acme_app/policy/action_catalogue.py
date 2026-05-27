"""Action catalogue mirror.

The DB is the source of truth, but this module mirrors the catalogue for fast
in-process checks. Eight types — the LLM cannot invent a ninth.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ActionDefinition:
    action_type: str
    label: str
    allowed_roles: tuple[str, ...]
    required_fields: tuple[str, ...]
    side_effect_level: str
    requires_confirmation: bool = True


CATALOGUE: dict[str, ActionDefinition] = {
    'ASSIGN_OWNER': ActionDefinition('ASSIGN_OWNER', 'Assign Owner', ('support_user', 'admin'), ('owner_name',), 'medium'),
    'REQUEST_MISSING_INFO': ActionDefinition('REQUEST_MISSING_INFO', 'Request Missing Info', ('support_user', 'admin'), ('description',), 'low'),
    'CUSTOMER_FOLLOW_UP': ActionDefinition('CUSTOMER_FOLLOW_UP', 'Customer Follow Up', ('support_user', 'admin'), ('due_at',), 'low'),
    'ESCALATE_ISSUE': ActionDefinition('ESCALATE_ISSUE', 'Escalate Issue', ('support_user', 'admin'), ('issue_ref',), 'high'),
    'PREPARE_RECOVERY_PLAN': ActionDefinition('PREPARE_RECOVERY_PLAN', 'Prepare Recovery Plan', ('support_user', 'admin'), ('due_at',), 'high'),
    'SCHEDULE_REVIEW': ActionDefinition('SCHEDULE_REVIEW', 'Schedule Review', ('support_user', 'admin'), ('due_at',), 'low'),
    'UPDATE_ISSUE_STATUS': ActionDefinition('UPDATE_ISSUE_STATUS', 'Update Issue Status', ('support_user', 'admin'), ('new_status',), 'medium'),
    'CREATE_EXEC_SUMMARY': ActionDefinition('CREATE_EXEC_SUMMARY', 'Create Executive Summary', ('admin',), ('description',), 'low'),
}

ALLOWED_ACTION_TYPES = frozenset(CATALOGUE)


def validate_action_type(action_type: str) -> bool:
    return action_type in CATALOGUE


def get_definition(action_type: str) -> ActionDefinition | None:
    return CATALOGUE.get(action_type)


def role_allowed(role: str, action_type: str) -> bool:
    defn = CATALOGUE.get(action_type)
    if defn is None:
        return False
    return role in defn.allowed_roles


def required_fields(action_type: str) -> tuple[str, ...]:
    defn = CATALOGUE.get(action_type)
    return defn.required_fields if defn else ()
