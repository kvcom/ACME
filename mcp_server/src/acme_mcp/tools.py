from datetime import datetime

from acme_mcp.db import get_conn


def search_customers(customer_name: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id::text, name, region FROM customers WHERE lower(name) LIKE lower(%s) ORDER BY name LIMIT 10", (f"%{customer_name}%",))
        rows = cur.fetchall()
    return {'matches': [{'customer_id': r[0], 'name': r[1], 'region': r[2]} for r in rows]}


def get_customer_profile(customer_name: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id::text, name, tier, industry, region, customer_timezone, account_owner FROM customers WHERE lower(name) LIKE lower(%s) ORDER BY name LIMIT 1", (f"%{customer_name}%",))
        row = cur.fetchone()
    if row is None:
        return {'not_found': True}
    return {'customer_id': row[0], 'name': row[1], 'tier': row[2], 'industry': row[3], 'region': row[4], 'customer_timezone': row[5], 'account_owner': row[6]}


def get_open_issues(customer_id: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT issue_ref, title, severity, status, sla_status FROM issues WHERE customer_id::text = %s AND status NOT IN ('Closed','Resolved')", (customer_id,))
        rows = cur.fetchall()
    return {'issues': [{'issue_ref': r[0], 'title': r[1], 'severity': r[2], 'status': r[3], 'sla_status': r[4]} for r in rows]}


def summarise_issue_history(issue_ref: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT i.issue_ref, i.title, i.description FROM issues i WHERE i.issue_ref=%s", (issue_ref,))
        issue = cur.fetchone()
        cur.execute("SELECT update_text FROM issue_updates u JOIN issues i ON i.id=u.issue_id WHERE i.issue_ref=%s ORDER BY created_at DESC LIMIT 3", (issue_ref,))
        updates = [r[0] for r in cur.fetchall()]
    if issue is None:
        return {'not_found': True}
    return {'issue_ref': issue[0], 'summary': f"{issue[1]}: {issue[2]}", 'latest_update': updates[0] if updates else '', 'evidence': [issue_ref]}


def recommend_next_action(issue_ref: str) -> dict:
    return {'action_type': 'PREPARE_RECOVERY_PLAN', 'priority': 'High', 'title': f'Prepare recovery plan for {issue_ref}', 'description': 'Create a written recovery plan covering root cause, owner and expected resolution date.', 'rationale': 'High-risk issue under SLA pressure', 'evidence': [issue_ref]}


def create_next_action(actor: dict, issue_ref: str, action_type: str, title: str, description: str, priority: str, due_at: str | None, evidence: list[str], idempotency_key: str, confirmation_token: str) -> dict:
    if not confirmation_token:
        return {'created': False, 'denied': True, 'reason': 'confirmation_token required'}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT action_ref FROM next_actions WHERE idempotency_key=%s', (idempotency_key,))
        existing = cur.fetchone()
        if existing:
            return {'created': False, 'duplicate': True, 'existing_action_ref': existing[0]}
        cur.execute('SELECT id::text, customer_id::text FROM issues WHERE issue_ref=%s', (issue_ref,))
        issue = cur.fetchone()
        if not issue:
            return {'created': False, 'denied': True, 'reason': 'Issue not found'}
        issue_id, customer_id = issue
        action_ref = f"NA-{int(datetime.utcnow().timestamp())}"
        cur.execute("INSERT INTO next_actions (action_ref, customer_id, issue_id, action_type, title, description, priority, status, owner_role, owner_name, due_at, rationale, evidence_json, created_by, created_by_role, idempotency_key) VALUES (%s,%s,%s,%s,%s,%s,%s,'Open',%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
            (action_ref, customer_id, issue_id, action_type, title, description, priority, actor.get('role'), actor.get('username'), due_at, 'Created via MCP', str(evidence).replace("'", '"'), actor.get('username'), actor.get('role'), idempotency_key))
    return {'created': True, 'action_ref': action_ref, 'status': 'Open'}


def update_next_action(actor: dict, action_ref: str, new_status: str, confirmation_token: str) -> dict:
    if not confirmation_token:
        return {'updated': False, 'reason': 'confirmation_token required'}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('UPDATE next_actions SET status=%s, updated_at=now() WHERE action_ref=%s', (new_status, action_ref))
    return {'updated': True, 'action_ref': action_ref, 'new_status': new_status}


def update_issue_status(actor: dict, issue_ref: str, new_status: str, confirmation_token: str) -> dict:
    if not confirmation_token:
        return {'updated': False, 'reason': 'confirmation_token required'}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('UPDATE issues SET status=%s, updated_at=now() WHERE issue_ref=%s', (new_status, issue_ref))
    return {'updated': True, 'issue_ref': issue_ref, 'new_status': new_status}
