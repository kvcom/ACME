"""Closure Readiness Check Skill (v1).

Returns ready_to_close + the specific missing information. Eval Case 6 depends
on this Skill being invoked when the query asks "can we close X".
"""
from __future__ import annotations

from typing import Any

VERSION = 'v1'


def run(
    issue_ref: str,
    issue: dict[str, Any] | None,
    updates: list[dict[str, Any]],
    open_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    open_actions = open_actions or []
    update_texts = ' '.join(u.get('update_text', '') for u in updates).lower()
    update_types = {u.get('update_type', '') for u in updates}

    has_resolution_note = any(t in update_texts for t in ('resolved', 'resolution', 'fixed', 'closed'))
    has_customer_acceptance = any(t in update_texts for t in ('customer accept', 'customer sign-off', 'customer approved', 'acceptance'))
    recovery_pending = any(
        a.get('action_type') == 'PREPARE_RECOVERY_PLAN' and a.get('status') not in ('Completed', 'Cancelled')
        for a in open_actions
    )
    blockers_open = (
        (issue and issue.get('status') in ('Escalated', 'Open', 'In Progress'))
        or recovery_pending
        or not has_resolution_note
    )

    missing: list[str] = []
    if not has_customer_acceptance:
        missing.append('Customer acceptance')
    if not has_resolution_note:
        missing.append('Technical resolution confirmation')
    if recovery_pending:
        missing.append('Recovery plan completion')
    if not update_types:
        missing.append('Any issue updates')

    ready = (not blockers_open) and has_customer_acceptance and not missing
    if ready:
        rec = {'action_type': 'UPDATE_ISSUE_STATUS', 'priority': 'Medium',
               'title': f'Close {issue_ref}', 'rationale': 'All closure conditions satisfied.'}
        reason = 'All closure conditions satisfied.'
    else:
        rec = {'action_type': 'REQUEST_MISSING_INFO', 'priority': 'High',
               'title': f'Request missing information for {issue_ref}',
               'rationale': 'Required closure artefacts are missing: ' + '; '.join(missing)}
        reason = 'Required closure artefacts are missing: ' + '; '.join(missing)

    return {
        'version': VERSION,
        'issue_ref': issue_ref,
        'ready_to_close': ready,
        'reason': reason,
        'missing_information': missing,
        'recommended_next_action': rec,
        'evidence': [f'issue:{issue_ref}'] + [f'update:{u.get("id", "")}' for u in updates[:3] if u.get('id')],
    }
