from dataclasses import dataclass, field
from datetime import datetime


PRIORITIES = ('Low', 'Medium', 'High', 'Critical')
ACTION_STATUSES = ('Proposed', 'Open', 'In Progress', 'Blocked', 'Completed', 'Cancelled')


@dataclass(frozen=True)
class ProposedAction:
    action_type: str
    title: str
    description: str
    priority: str
    issue_ref: str | None
    customer_id: str
    rationale: str
    evidence: tuple[str, ...] = field(default_factory=tuple)
    due_at: datetime | None = None
    confirmation_token: str = ''
    idempotency_key: str = ''
    created_from_trace_ref: str = ''


@dataclass(frozen=True)
class NextAction:
    action_ref: str
    action_type: str
    title: str
    description: str
    priority: str
    status: str
    customer_id: str
    issue_id: str | None
    owner_role: str | None
    owner_name: str | None
    due_at: datetime | None
    rationale: str
    evidence: tuple[str, ...]
    created_by: str
    created_by_role: str
    idempotency_key: str
    created_at: datetime
