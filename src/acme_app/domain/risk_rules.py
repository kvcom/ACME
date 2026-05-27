"""Deterministic risk classification.

LLM narrates, rules classify. Given the same inputs this function must always
produce the same risk level — eval cases depend on it.
"""
from dataclasses import dataclass

from acme_app.domain.customer import Customer
from acme_app.domain.issue import Issue


RISK_LEVELS = ('Low', 'Medium', 'High', 'Critical')


@dataclass(frozen=True)
class RiskAssessment:
    level: str
    factors: tuple[str, ...]


def classify(customer: Customer, issue: Issue) -> RiskAssessment:
    factors: list[str] = []

    if customer.is_high_value:
        factors.append(f'{customer.tier} customer')
    if issue.severity == 'P1':
        factors.append('P1 issue')
    elif issue.severity == 'P2':
        factors.append('P2 issue')
    if issue.sla_status == 'Breached':
        factors.append('SLA breached')
    elif issue.sla_status == 'At Risk':
        factors.append('SLA at risk')
    if issue.stale_update_count >= 2:
        factors.append(f'{issue.stale_update_count} day(s) since last update')
    if issue.owner is None:
        factors.append('No owner assigned')

    if customer.is_high_value and issue.severity == 'P1' and issue.sla_status == 'Breached':
        level = 'Critical'
    elif issue.severity == 'P1':
        level = 'High'
    elif issue.severity == 'P2' and issue.sla_status == 'At Risk' and issue.stale_update_count >= 2:
        level = 'High'
    elif (issue.is_open and issue.stale_update_count >= 5) or issue.owner is None:
        level = 'Medium'
    else:
        level = 'Low'

    return RiskAssessment(level=level, factors=tuple(factors))


def recommended_action_for(risk: RiskAssessment, issue: Issue) -> tuple[str, str]:
    """Return (action_type, priority) deterministically from risk."""
    if risk.level == 'Critical':
        return ('PREPARE_RECOVERY_PLAN', 'Critical')
    if risk.level == 'High':
        if issue.severity == 'P1':
            return ('PREPARE_RECOVERY_PLAN', 'High')
        return ('ESCALATE_ISSUE', 'High')
    if risk.level == 'Medium':
        if issue.owner is None:
            return ('ASSIGN_OWNER', 'Medium')
        return ('CUSTOMER_FOLLOW_UP', 'Medium')
    return ('SCHEDULE_REVIEW', 'Low')


def classify_simple(tier: str, severity: str, sla_status: str, stale_updates: int = 0) -> str:
    """Legacy convenience for callers that don't have full Customer/Issue objects."""
    if tier in {'Enterprise', 'Strategic'} and severity == 'P1' and sla_status == 'Breached':
        return 'Critical'
    if severity == 'P1' or (severity == 'P2' and sla_status == 'At Risk' and stale_updates >= 2):
        return 'High'
    if stale_updates > 0:
        return 'Medium'
    return 'Low'
