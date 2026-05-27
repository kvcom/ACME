from dataclasses import dataclass, field
from datetime import datetime


SEVERITIES = ('P1', 'P2', 'P3', 'P4')
STATUSES = ('Open', 'In Progress', 'Waiting for Customer', 'Escalated', 'Resolved', 'Closed')
SLA_STATES = ('Within SLA', 'At Risk', 'Breached')


@dataclass(frozen=True)
class IssueUpdate:
    update_text: str
    update_type: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class Issue:
    issue_ref: str
    customer_id: str
    title: str
    description: str
    severity: str
    status: str
    sla_status: str
    owner: str | None
    opened_at: datetime
    updates: tuple[IssueUpdate, ...] = field(default_factory=tuple)

    @property
    def is_open(self) -> bool:
        return self.status not in ('Resolved', 'Closed')

    @property
    def stale_update_count(self) -> int:
        """Count of distinct days since last update — coarse staleness signal."""
        if not self.updates:
            return 999
        latest = max(u.created_at for u in self.updates)
        delta = datetime.now(tz=latest.tzinfo) - latest
        return max(0, delta.days)
