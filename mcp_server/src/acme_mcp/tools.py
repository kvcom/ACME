"""MCP tool implementations.

Every write tool requires a confirmation_token (HMAC) which is verified using a
shared secret. The MCP server enforces:

  - action_type ∈ ALLOWED_ACTION_TYPES
  - role ∈ allowed roles for the action_type
  - idempotency_key uniqueness
  - confirmation_token signature & expiry

This is the second gate (the app is the first). It exists so the MCP server is
safe to expose to other clients in future.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from typing import Any

from acme_mcp import recommendation_engine
from acme_mcp.db import get_conn
from acme_mcp.validation import ALLOWED_ISSUE_STATUSES, allowed_action_types, role_may_create


HMAC_SECRET = os.getenv('CONFIRMATION_HMAC_SECRET', 'dev-only-secret-change-me')


def _verify_token(token: str, expected_action_type: str | None = None, expected_issue_ref: str | None = None) -> tuple[bool, str]:
    parts = token.split('|')
    if len(parts) != 5:
        return False, 'token malformed'
    trace_ref, action_type, issue_ref, expires_at_s, sig = parts
    payload = f'{trace_ref}|{action_type}|{issue_ref}|{expires_at_s}'
    expected = hmac.new(HMAC_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False, 'signature mismatch'
    try:
        if time.time() > int(expires_at_s):
            return False, 'token expired'
    except ValueError:
        return False, 'expiry malformed'
    if expected_action_type and action_type != expected_action_type:
        return False, f'token action_type mismatch (got {action_type}, expected {expected_action_type})'
    if expected_issue_ref and issue_ref != expected_issue_ref:
        return False, f'token issue_ref mismatch (got {issue_ref}, expected {expected_issue_ref})'
    return True, 'ok'


def search_customers(customer_name: str) -> dict[str, Any]:
    name = (customer_name or '').strip()
    with get_conn() as conn, conn.cursor() as cur:
        if name:
            cur.execute(
                "SELECT id::text, name, region, tier FROM customers WHERE lower(name) LIKE lower(%s) ORDER BY name LIMIT 10",
                (f'%{name}%',),
            )
        else:
            cur.execute("SELECT id::text, name, region, tier FROM customers ORDER BY name LIMIT 25")
        rows = cur.fetchall()
    return {'matches': [{'customer_id': r[0], 'name': r[1], 'region': r[2], 'tier': r[3]} for r in rows]}


def get_customer_profile(customer_name: str) -> dict[str, Any]:
    """Return a single customer profile.

    Refuses to pick when the query is ambiguous — returns ``multiple_matches``
    with the candidate list instead. The agent is responsible for surfacing
    the disambiguation to the user; the MCP server never silently chooses
    between customers (Decision Ledger principle).
    """
    with get_conn() as conn, conn.cursor() as cur:
        # 1) Exact case-insensitive match — wins unambiguously.
        cur.execute(
            "SELECT id::text, name, tier, industry, region, customer_timezone, account_owner "
            "FROM customers WHERE lower(name)=lower(%s) LIMIT 2",
            (customer_name,),
        )
        rows = cur.fetchall()
        if not rows:
            # 2) Fall back to fuzzy substring; gather up to 5 candidates.
            cur.execute(
                "SELECT id::text, name, tier, industry, region, customer_timezone, account_owner "
                "FROM customers WHERE lower(name) LIKE lower(%s) ORDER BY name LIMIT 5",
                (f'%{customer_name}%',),
            )
            rows = cur.fetchall()

    if not rows:
        return {'not_found': True, 'queried': customer_name}
    if len(rows) > 1:
        return {
            'multiple_matches': True,
            'queried': customer_name,
            'matches': [
                {'customer_id': r[0], 'name': r[1], 'region': r[4], 'tier': r[2]}
                for r in rows
            ],
        }
    row = rows[0]
    return {
        'customer_id': row[0], 'name': row[1], 'tier': row[2], 'industry': row[3],
        'region': row[4], 'customer_timezone': row[5], 'account_owner': row[6],
    }


def _resolve_customer(cur, customer_id: str | None, customer_name: str | None) -> tuple[str | None, list[dict[str, str]]]:
    """Return (single_customer_id, candidate_list).

    - If `customer_id` is given, trust it.
    - Else if `customer_name` matches exactly one row, return that id.
    - Else return None and the candidate list so the caller can surface a
      disambiguation hint instead of silently picking one.
    """
    if customer_id:
        return customer_id, []
    if not customer_name:
        return None, []
    cur.execute(
        "SELECT id::text, name, region, tier FROM customers WHERE lower(name)=lower(%s) LIMIT 2",
        (customer_name,),
    )
    rows = cur.fetchall()
    if not rows:
        cur.execute(
            "SELECT id::text, name, region, tier FROM customers WHERE lower(name) LIKE lower(%s) ORDER BY name LIMIT 5",
            (f'%{customer_name}%',),
        )
        rows = cur.fetchall()
    if len(rows) == 1:
        return rows[0][0], []
    candidates = [{'customer_id': r[0], 'name': r[1], 'region': r[2], 'tier': r[3]} for r in rows]
    return None, candidates


def get_open_issues(customer_id: str | None = None, customer_name: str | None = None) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cust_id, candidates = _resolve_customer(cur, customer_id, customer_name)
        if cust_id is None:
            if candidates:
                return {
                    'issues': [],
                    'multiple_matches': True,
                    'queried': customer_name,
                    'matches': candidates,
                }
            return {'issues': [], 'not_found': True}
        cur.execute(
            "SELECT issue_ref, title, severity, status, sla_status, owner "
            "FROM issues WHERE customer_id::text=%s AND status NOT IN ('Closed','Resolved') "
            "ORDER BY CASE severity WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END",
            (cust_id,),
        )
        rows = cur.fetchall()
    return {
        'customer_id': cust_id,
        'issues': [
            {'issue_ref': r[0], 'title': r[1], 'severity': r[2], 'status': r[3], 'sla_status': r[4], 'owner': r[5]}
            for r in rows
        ],
    }


def summarise_issue_history(issue_ref: str) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT i.issue_ref, i.title, i.description, i.severity, i.status, i.sla_status, i.owner, c.name, c.tier "
            "FROM issues i JOIN customers c ON c.id = i.customer_id WHERE i.issue_ref=%s",
            (issue_ref,),
        )
        issue = cur.fetchone()
        if issue is None:
            return {'not_found': True, 'issue_ref': issue_ref}
        cur.execute(
            "SELECT u.id::text, u.update_text, u.update_type, u.created_by, u.created_at "
            "FROM issue_updates u JOIN issues i ON i.id=u.issue_id WHERE i.issue_ref=%s "
            "ORDER BY u.created_at DESC LIMIT 8",
            (issue_ref,),
        )
        updates = [
            {'id': r[0], 'update_text': r[1], 'update_type': r[2], 'created_by': r[3], 'created_at': r[4].isoformat()}
            for r in cur.fetchall()
        ]
    return {
        'issue_ref': issue[0],
        'title': issue[1],
        'description': issue[2],
        'severity': issue[3],
        'status': issue[4],
        'sla_status': issue[5],
        'owner': issue[6],
        'customer_name': issue[7],
        'customer_tier': issue[8],
        'summary': f'{issue[1]}: {issue[2]}',
        'latest_update': updates[0]['update_text'] if updates else '',
        'updates': updates,
        'evidence': [f'issue:{issue[0]}'] + [f'update:{u["id"]}' for u in updates[:3]],
    }


def recommend_next_action(issue_ref: str) -> dict[str, Any]:
    history = summarise_issue_history(issue_ref)
    if history.get('not_found'):
        return {'not_found': True, 'issue_ref': issue_ref}
    severity = history.get('severity', 'P3')
    sla = history.get('sla_status', 'Within SLA')
    tier = history.get('customer_tier', 'Mid-market')
    owner = history.get('owner')

    # D-020: action_type selection is data-driven. Rules live in the
    # `action_recommendation_rules` table; the engine picks the first
    # matching one (sorted by priority_order). The fallback below only
    # fires if the engine snapshot is empty (DB outage / table empty).
    facts = {'tier': tier, 'severity': severity, 'sla': sla, 'owner': owner}
    engine_rec = recommendation_engine.evaluate('recommend_next_action_tool', facts)
    if engine_rec is not None:
        action_type = engine_rec['action_type']
        priority = engine_rec['priority']
        rationale = engine_rec['rationale'] or f'{tier} customer, {severity} issue, SLA {sla}.'
    else:
        action_type, priority = 'CUSTOMER_FOLLOW_UP', 'Medium'
        rationale = f'{tier} customer, {severity} issue, SLA {sla}.'

    return {
        'issue_ref': issue_ref,
        'action_type': action_type,
        'priority': priority,
        'title': f'{action_type.replace("_", " ").title()} for {history.get("customer_name", "customer")} {issue_ref}',
        'description': f'Recommended in response to {severity} issue (SLA: {sla}).',
        'rationale': rationale,
        'evidence': history.get('evidence', []),
    }


def create_next_action(
    actor: dict[str, Any],
    issue_ref: str,
    action_type: str,
    title: str,
    description: str,
    priority: str,
    due_at: str | None,
    evidence: list[str],
    idempotency_key: str,
    confirmation_token: str,
) -> dict[str, Any]:
    if action_type not in allowed_action_types():
        return {'created': False, 'denied': True, 'reason': f'Unknown action_type: {action_type}'}
    role = actor.get('role', '')
    if not role_may_create(role, action_type):
        return {'created': False, 'denied': True, 'reason': f'{role} cannot create {action_type}'}
    ok, reason = _verify_token(confirmation_token, expected_action_type=action_type, expected_issue_ref=issue_ref)
    if not ok:
        return {'created': False, 'denied': True, 'reason': f'confirmation_token invalid: {reason}'}

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT action_ref FROM next_actions WHERE idempotency_key=%s', (idempotency_key,))
        existing = cur.fetchone()
        if existing:
            conn.commit()
            return {'created': False, 'duplicate': True, 'existing_action_ref': existing[0]}
        cur.execute('SELECT id::text, customer_id::text FROM issues WHERE issue_ref=%s', (issue_ref,))
        issue = cur.fetchone()
        if not issue:
            return {'created': False, 'denied': True, 'reason': f'Issue {issue_ref} not found'}
        issue_id, customer_id = issue
        action_ref = f'NA-{int(datetime.utcnow().timestamp() * 1000) % 10_000_000}'
        # D-017: created_by_user_id is the live FK to the proposing user;
        # created_by + created_by_role stay as historical snapshots.
        # Resolved via sub-select against users(username); NULL when the
        # actor isn't a known system user (shouldn't happen in normal flow).
        cur.execute(
            """
            INSERT INTO next_actions (
                action_ref, customer_id, issue_id, action_type, title, description, priority, status,
                owner_role, owner_name, due_at, rationale, evidence_json,
                created_by_user_id, created_by, created_by_role,
                idempotency_key
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, 'Open',
                %s, %s, %s, %s, %s::jsonb,
                (SELECT id FROM users WHERE username = %s), %s, %s,
                %s
            )
            """,
            (
                action_ref, customer_id, issue_id, action_type, title, description, priority,
                role, actor.get('username'), due_at, 'Created via MCP create_next_action',
                json.dumps(evidence or []),
                actor.get('username', ''), actor.get('username', ''), role,
                idempotency_key,
            ),
        )
        conn.commit()
    return {'created': True, 'action_ref': action_ref, 'status': 'Open'}


def update_next_action(
    actor: dict[str, Any],
    action_ref: str,
    new_status: str,
    confirmation_token: str,
) -> dict[str, Any]:
    role = actor.get('role', '')
    if new_status == 'Cancelled' and role != 'admin':
        return {'updated': False, 'denied': True, 'reason': 'Only admin may cancel actions'}
    if role not in ('support_user', 'admin'):
        return {'updated': False, 'denied': True, 'reason': f'{role} cannot update actions'}
    # Bind the token to this specific action_ref so a token minted for one
    # action cannot be replayed against another. The token's resource slot
    # carries the action_ref for update_next_action proposals (see propose_confirm).
    ok, reason = _verify_token(
        confirmation_token,
        expected_action_type='UPDATE_NEXT_ACTION',
        expected_issue_ref=action_ref,
    )
    if not ok:
        return {'updated': False, 'denied': True, 'reason': f'confirmation_token invalid: {reason}'}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE next_actions SET status=%s, updated_at=now(), completed_at=CASE WHEN %s=\'Completed\' THEN now() ELSE completed_at END WHERE action_ref=%s RETURNING action_ref',
            (new_status, new_status, action_ref),
        )
        row = cur.fetchone()
        conn.commit()
    if row is None:
        return {'updated': False, 'denied': True, 'reason': f'action {action_ref} not found'}
    return {'updated': True, 'action_ref': action_ref, 'new_status': new_status}


def update_issue_status(
    actor: dict[str, Any],
    issue_ref: str,
    new_status: str,
    confirmation_token: str,
) -> dict[str, Any]:
    if new_status not in ALLOWED_ISSUE_STATUSES:
        return {'updated': False, 'denied': True, 'reason': f'Unknown issue status: {new_status}'}
    role = actor.get('role', '')
    if role not in ('support_user', 'admin'):
        return {'updated': False, 'denied': True, 'reason': f'{role} cannot update issue status'}
    ok, reason = _verify_token(
        confirmation_token,
        expected_action_type='UPDATE_ISSUE_STATUS',
        expected_issue_ref=issue_ref,
    )
    if not ok:
        return {'updated': False, 'denied': True, 'reason': f'confirmation_token invalid: {reason}'}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE issues SET status=%s, updated_at=now() WHERE issue_ref=%s RETURNING issue_ref',
            (new_status, issue_ref),
        )
        row = cur.fetchone()
        conn.commit()
    if row is None:
        return {'updated': False, 'denied': True, 'reason': f'issue {issue_ref} not found'}
    return {'updated': True, 'issue_ref': issue_ref, 'new_status': new_status}
