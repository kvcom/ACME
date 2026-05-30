"""Customer Escalation Summary Skill (v1).

Reusable, versioned, schema-based. Pure function — depends only on inputs
passed in by the orchestrator. Risk classification is delegated to
domain.risk_rules so this Skill stays deterministic.
"""
from __future__ import annotations

from typing import Any

from acme_app.domain.risk_rules import classify_simple
from acme_app.policy import recommendation_engine


VERSION = 'v1'


# Fallback used only if the engine returns None (e.g. DB outage at startup
# before refresh_from_db() ran). Matches the old "if nothing else matches,
# recommend SCHEDULE_REVIEW / Low" branch.
_RECOMMENDER = 'customer_escalation_summary'
_FALLBACK_ACTION = {'action_type': 'SCHEDULE_REVIEW', 'priority': 'Low'}


def _highest_severity(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return 'P4'
    order = {'P1': 1, 'P2': 2, 'P3': 3, 'P4': 4}
    return sorted(issues, key=lambda i: order.get(i.get('severity', 'P4'), 4))[0].get('severity', 'P4')


def _worst_sla(issues: list[dict[str, Any]]) -> str:
    rank = {'Breached': 0, 'At Risk': 1, 'Within SLA': 2}
    if not issues:
        return 'Within SLA'
    return sorted(issues, key=lambda i: rank.get(i.get('sla_status', 'Within SLA'), 2))[0].get('sla_status', 'Within SLA')


def _recommend(risk: str, has_owner: bool, severity: str) -> dict[str, str]:
    # D-020: action_type selection moved to action_recommendation_rules.
    # The skill computes the facts (risk, has_owner, severity) and asks the
    # engine which rule matches. Operators can change the mapping by editing
    # the table — no code change. Fallback is used only if the engine
    # snapshot is empty (DB outage at startup).
    rec = recommendation_engine.evaluate(_RECOMMENDER, {
        'risk': risk, 'has_owner': has_owner, 'severity': severity,
    })
    if rec is None:
        return dict(_FALLBACK_ACTION)
    return {'action_type': rec.action_type, 'priority': rec.priority}


def run(
    customer: dict[str, Any],
    issues: list[dict[str, Any]],
    updates: list[dict[str, Any]],
    actor_role: str = 'sales_user',
) -> dict[str, Any]:
    if not customer:
        return {
            'version': VERSION,
            'executive_summary': 'Customer not found.',
            'risk_level': 'Low',
            'risk_factors': [],
            'recommended_next_action': {'action_type': 'SCHEDULE_REVIEW', 'priority': 'Low', 'title': '', 'rationale': ''},
            'missing_information': ['Customer identity'],
            'evidence': [],
        }

    severity = _highest_severity(issues)
    sla = _worst_sla(issues)
    stale = max(0, len(updates) - 1)
    risk_level = classify_simple(customer.get('tier', 'Mid-market'), severity, sla, stale)

    factors: list[str] = []
    if customer.get('tier') in ('Enterprise', 'Strategic'):
        factors.append(f"{customer['tier']} customer")
    if severity == 'P1':
        factors.append('P1 issue')
    elif severity == 'P2':
        factors.append('P2 issue')
    if sla == 'Breached':
        factors.append('SLA breached')
    elif sla == 'At Risk':
        factors.append('SLA at risk')
    if stale >= 2:
        factors.append(f'{stale} update(s) since last engineering touch')

    open_issues = [i for i in issues if i.get('status') not in ('Resolved', 'Closed')]
    has_owner = any(i.get('owner') for i in open_issues)
    rec = _recommend(risk_level, has_owner, severity)
    issue_refs = [i.get('issue_ref') for i in open_issues if i.get('issue_ref')]
    representative_issue = open_issues[0].get('issue_ref', '') if open_issues else ''

    rec_full = {
        'action_type': rec['action_type'],
        'priority': rec['priority'],
        'title': f"{rec['action_type'].replace('_', ' ').title()} for {customer.get('name', 'customer')}"
                 + (f' on {representative_issue}' if representative_issue else ''),
        'rationale': 'Deterministic risk rules; ' + ', '.join(factors or ['no notable risk factors']),
    }

    missing: list[str] = []
    if open_issues and not any('resolution' in (u.get('update_text', '').lower()) for u in updates):
        missing.append('Confirmed engineering resolution date')
    if not any('customer' in u.get('update_type', '').lower() for u in updates):
        missing.append('Latest customer sentiment')

    summary = (
        f"{customer.get('name', 'Customer')} ({customer.get('tier', '')}, {customer.get('region', '')}) "
        f"has {len(open_issues)} open issue(s); highest severity {severity}, SLA {sla}. "
        f"Risk: {risk_level}. Recommended: {rec_full['action_type']} ({rec['priority']})."
    )

    evidence: list[str] = [f'customer:{customer.get("customer_id", customer.get("name", ""))}']
    evidence.extend(f'issue:{ref}' for ref in issue_refs)
    for u in updates[:3]:
        if u.get('id'):
            evidence.append(f'update:{u["id"]}')

    return {
        'version': VERSION,
        'executive_summary': summary,
        'risk_level': risk_level,
        'risk_factors': factors,
        'recommended_next_action': rec_full,
        'missing_information': missing,
        'evidence': evidence,
    }
