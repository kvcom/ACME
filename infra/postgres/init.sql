CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Identity tables. Keycloak still issues the JWT (authentication), but
-- Postgres is the source of truth for *which roles a user has*
-- (authorization). See DECISION_LOG D-016. The username column matches the
-- Keycloak `preferred_username` claim.
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT NOT NULL UNIQUE,
    email TEXT,
    display_name TEXT,
    keycloak_subject TEXT UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS user_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Append-only model (D-017): role rows are deactivated, never deleted.
    -- ON DELETE CASCADE is retained as a backstop for dev wipes only — the
    -- app code never deletes a `users` row.
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by TEXT,
    revoked_at TIMESTAMPTZ,
    revoked_by TEXT,
    CONSTRAINT unique_user_role UNIQUE (user_id, role_name),
    CONSTRAINT role_name_supported CHECK (role_name IN ('sales_user','support_user','admin'))
);

CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id);

CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    industry TEXT NOT NULL,
    tier TEXT NOT NULL,
    region TEXT NOT NULL,
    customer_timezone TEXT NOT NULL DEFAULT 'UTC',
    account_owner TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS issues (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_ref TEXT NOT NULL UNIQUE,
    customer_id UUID NOT NULL REFERENCES customers(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    sla_status TEXT NOT NULL,
    owner TEXT,
    opened_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS issue_updates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id UUID NOT NULL REFERENCES issues(id),
    update_text TEXT NOT NULL,
    update_type TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS action_catalogue (
    action_type TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    allowed_roles TEXT[] NOT NULL,
    required_fields TEXT[] NOT NULL,
    side_effect_level TEXT NOT NULL,
    requires_confirmation BOOLEAN NOT NULL DEFAULT true,
    is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS next_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_ref TEXT NOT NULL UNIQUE,
    customer_id UUID NOT NULL REFERENCES customers(id),
    issue_id UUID REFERENCES issues(id),
    action_type TEXT NOT NULL REFERENCES action_catalogue(action_type),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    owner_role TEXT,
    owner_name TEXT,
    due_at TIMESTAMPTZ,
    rationale TEXT NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '[]',
    -- Identity link (D-017): created_by_user_id is the live FK to the
    -- system user who proposed the action; created_by + created_by_role
    -- stay as historical snapshots.
    created_by_user_id UUID REFERENCES users(id),
    created_by TEXT NOT NULL,
    created_by_role TEXT NOT NULL,
    created_from_trace_id UUID,
    idempotency_key TEXT,
    parent_action_id UUID REFERENCES next_actions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT unique_idempotency UNIQUE (idempotency_key)
);

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_ref TEXT NOT NULL UNIQUE,
    -- Identity link (D-017): user_id is the live FK, username is the
    -- historical display snapshot. Both are kept so audit reads still show
    -- what the user was called at the time.
    user_id UUID REFERENCES users(id),
    username TEXT NOT NULL,
    title TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_preview TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    -- Soft-delete only — the underlying agent_traces / trace_events /
    -- tool_call_logs / rbac_decisions rows are NEVER removed (Decision Ledger
    -- principle, plan_v2 §2.5). This column just hides the conversation from
    -- the user-facing sidebar and history endpoints.
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_traces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_ref TEXT NOT NULL UNIQUE,
    otel_trace_id TEXT,
    conversation_id UUID REFERENCES conversations(id),
    -- Identity link (D-017): user_id is the live FK, username + user_role
    -- are the historical display snapshots frozen at write time.
    user_id UUID REFERENCES users(id),
    username TEXT NOT NULL,
    user_role TEXT NOT NULL,
    user_query TEXT NOT NULL,
    user_query_redacted TEXT NOT NULL,
    detected_intent TEXT,
    final_answer TEXT,
    final_status TEXT NOT NULL,
    llm_provider TEXT NOT NULL,
    llm_model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0,
    llm_latency_ms INTEGER,
    tool_latency_ms INTEGER,
    total_latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trace_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL REFERENCES agent_traces(id),
    event_type TEXT NOT NULL,
    event_name TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    latency_ms INTEGER,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tool_call_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID REFERENCES agent_traces(id),
    tool_name TEXT NOT NULL,
    input_json JSONB NOT NULL DEFAULT '{}',
    output_summary JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    latency_ms INTEGER,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rbac_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID REFERENCES agent_traces(id),
    username TEXT NOT NULL,
    role_name TEXT NOT NULL,
    operation TEXT NOT NULL,
    resource TEXT NOT NULL,
    allowed BOOLEAN NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_run_ref TEXT NOT NULL UNIQUE,
    llm_provider TEXT NOT NULL,
    llm_model TEXT NOT NULL,
    git_sha TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    cases_total INTEGER NOT NULL DEFAULT 0,
    cases_passed INTEGER NOT NULL DEFAULT 0,
    total_cost_usd NUMERIC(10, 6)
);

CREATE TABLE IF NOT EXISTS eval_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_run_id UUID REFERENCES eval_runs(id),
    case_id TEXT NOT NULL,
    query TEXT NOT NULL,
    -- Identity link (D-017): every eval case runs under one of the three
    -- permanent eval-persona users (eval.sales / eval.support / eval.admin),
    -- which connects the eval island to the identity graph. role_name is
    -- still kept as a historical snapshot.
    user_id UUID REFERENCES users(id),
    role_name TEXT NOT NULL,
    expected_tools TEXT[] NOT NULL,
    actual_tools TEXT[] NOT NULL,
    tool_selection_pass BOOLEAN NOT NULL,
    grounding_pass BOOLEAN NOT NULL,
    rbac_pass BOOLEAN NOT NULL,
    action_reasonableness_pass BOOLEAN NOT NULL,
    adversarial_pass BOOLEAN,
    latency_ms INTEGER,
    cost_usd NUMERIC(10,6),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Active-row views (D-017) ────────────────────────────────────────────────
-- The application reads listings from these views so the "WHERE is_active"
-- filter cannot be forgotten. Audit and trace endpoints continue to read
-- from the base tables directly.

CREATE OR REPLACE VIEW v_active_users AS
    SELECT * FROM users
    WHERE is_active = true AND deleted_at IS NULL;

CREATE OR REPLACE VIEW v_active_user_roles AS
    SELECT ur.* FROM user_roles ur
    JOIN users u ON u.id = ur.user_id
    WHERE ur.is_active = true
      AND u.is_active = true
      AND u.deleted_at IS NULL;

CREATE OR REPLACE VIEW v_active_customers AS
    SELECT * FROM customers
    WHERE status = 'active';

CREATE OR REPLACE VIEW v_active_conversations AS
    SELECT * FROM conversations
    WHERE deleted_at IS NULL;

-- ─── GDPR Article 17 erasure path (D-017) ────────────────────────────────────
-- Overwrites PII columns for one user's rows without changing row counts.
-- Builds on the at-ingest PII redactor (D-012) — user_query_redacted is
-- already scrubbed, so this function targets the raw user_query column
-- and the user's own profile fields.

CREATE OR REPLACE FUNCTION redact_user_pii(target_user_id UUID)
RETURNS TABLE(
    users_redacted INT,
    traces_redacted INT,
    issue_updates_redacted INT,
    next_actions_redacted INT
) AS $$
DECLARE
    target_username TEXT;
    u_count INT := 0;
    t_count INT := 0;
    iu_count INT := 0;
    na_count INT := 0;
BEGIN
    SELECT username INTO target_username FROM users WHERE id = target_user_id;
    IF target_username IS NULL THEN
        RAISE EXCEPTION 'redact_user_pii: no user with id %', target_user_id;
    END IF;

    UPDATE users
       SET email = '[REDACTED-GDPR]',
           display_name = '[REDACTED-GDPR]'
     WHERE id = target_user_id;
    GET DIAGNOSTICS u_count = ROW_COUNT;

    UPDATE agent_traces
       SET user_query = '[REDACTED-GDPR]'
     WHERE user_id = target_user_id
        OR username = target_username;
    GET DIAGNOSTICS t_count = ROW_COUNT;

    -- Free-text the subject authored can carry their personal data, so it is
    -- redacted too. issue_updates links to the actor only by name snapshot
    -- (created_by); next_actions has the live FK plus the snapshot.
    UPDATE issue_updates
       SET update_text = '[REDACTED-GDPR]'
     WHERE created_by = target_username;
    GET DIAGNOSTICS iu_count = ROW_COUNT;

    UPDATE next_actions
       SET description = '[REDACTED-GDPR]'
     WHERE created_by_user_id = target_user_id
        OR created_by = target_username;
    GET DIAGNOSTICS na_count = ROW_COUNT;

    users_redacted := u_count;
    traces_redacted := t_count;
    issue_updates_redacted := iu_count;
    next_actions_redacted := na_count;
    RETURN NEXT;
END;
$$ LANGUAGE plpgsql;

-- ─── Realtime change notifications (D-018) ───────────────────────────────────
-- AFTER INSERT/UPDATE triggers fire pg_notify('db_explorer', ...) so the
-- backend's LISTEN-based broadcaster can push live updates over WebSocket
-- to the admin DB Explorer.

CREATE OR REPLACE FUNCTION notify_db_explorer() RETURNS TRIGGER AS $$
DECLARE
    row_data JSONB;
    row_id TEXT;
BEGIN
    -- Use OLD on DELETE (NEW is NULL); NEW otherwise.
    IF TG_OP = 'DELETE' THEN
        row_data := row_to_json(OLD)::jsonb;
    ELSE
        row_data := row_to_json(NEW)::jsonb;
    END IF;
    row_id := COALESCE(row_data ->> 'id', row_data ->> 'action_type');
    PERFORM pg_notify('db_explorer', json_build_object(
        'table', TG_TABLE_NAME,
        'op', TG_OP,
        'id', row_id
    )::text);
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t TEXT;
    tables TEXT[] := ARRAY[
        'users', 'user_roles',
        'customers', 'issues', 'issue_updates', 'next_actions', 'action_catalogue',
        'conversations', 'agent_traces', 'trace_events', 'tool_call_logs', 'rbac_decisions',
        'eval_runs', 'eval_results'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS notify_db_explorer_trg ON %I', t);
        EXECUTE format(
            'CREATE TRIGGER notify_db_explorer_trg
             AFTER INSERT OR UPDATE OR DELETE ON %I
             FOR EACH ROW EXECUTE FUNCTION notify_db_explorer()',
            t
        );
    END LOOP;
END $$;
-- Migration for DECISION_LOG D-020 — data-driven action recommendations.
-- Creates `action_recommendation_rules` and seeds it with every existing
-- hardcoded rule from the three recommenders, in their original priority
-- order. Idempotent.
--
-- Each row is one branch of one recommender. Engine semantics:
--   • rules filtered by `recommender` and `is_active=true`
--   • sorted by `priority_order` ascending
--   • first whose conditions all match wins
--   • conditions are JSONB: { "field": <value-or-operator-object>, ... }
--   • supported operators: {"in":[...]}, {"not_in":[...]},
--     {"null":true}, {"not_null":true}; bare value means equality
--   • {} means "always match" (fallback rule)

CREATE TABLE IF NOT EXISTS action_recommendation_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_ref TEXT NOT NULL UNIQUE,
    -- Which recommender this rule applies to. Today: one of
    --   'recommend_next_action_tool'  — MCP tool, inputs: tier, severity,
    --                                    sla, owner
    --   'customer_escalation_summary' — app skill, inputs: risk, has_owner,
    --                                    severity
    --   'closure_readiness_check'     — app skill, inputs: ready_to_close
    recommender TEXT NOT NULL,
    priority_order INTEGER NOT NULL,
    conditions JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_type TEXT NOT NULL REFERENCES action_catalogue(action_type),
    recommended_priority TEXT NOT NULL,
    rationale_template TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes TEXT,
    CONSTRAINT priority_supported CHECK (recommended_priority IN ('Low','Medium','High','Critical'))
);

CREATE INDEX IF NOT EXISTS idx_recommendation_rules_recommender
    ON action_recommendation_rules(recommender, priority_order)
    WHERE is_active;

-- Attach the realtime trigger (D-018) so engine snapshots refresh on change.
DROP TRIGGER IF EXISTS notify_db_explorer_trg ON action_recommendation_rules;
CREATE TRIGGER notify_db_explorer_trg
    AFTER INSERT OR UPDATE OR DELETE ON action_recommendation_rules
    FOR EACH ROW EXECUTE FUNCTION notify_db_explorer();

-- ─── Seed rules ────────────────────────────────────────────────────────────
-- recommend_next_action MCP tool (mcp_server/src/acme_mcp/tools.py:203)
-- Original branches, top-to-bottom:
INSERT INTO action_recommendation_rules
    (rule_ref, recommender, priority_order, conditions, action_type, recommended_priority, rationale_template, notes)
VALUES
('tool:critical_enterprise_p1_breach', 'recommend_next_action_tool', 10,
 '{"tier":{"in":["Enterprise","Strategic"]},"severity":"P1","sla":"Breached"}'::jsonb,
 'PREPARE_RECOVERY_PLAN', 'Critical',
 '{tier} customer, {severity} issue, SLA {sla}.',
 'Top-priority tier + worst severity + worst SLA = critical recovery plan.'),

('tool:high_severity_p1',              'recommend_next_action_tool', 20,
 '{"severity":"P1"}'::jsonb,
 'PREPARE_RECOVERY_PLAN', 'High',
 '{tier} customer, {severity} issue, SLA {sla}.',
 'Any other P1 still warrants a recovery plan, just not critical.'),

('tool:p2_at_risk',                    'recommend_next_action_tool', 30,
 '{"severity":"P2","sla":"At Risk"}'::jsonb,
 'ESCALATE_ISSUE', 'High',
 '{tier} customer, {severity} issue, SLA {sla}.',
 'P2 at risk of breaching SLA — escalate before it goes Breached.'),

('tool:owner_missing',                 'recommend_next_action_tool', 40,
 '{"owner":{"null":true}}'::jsonb,
 'ASSIGN_OWNER', 'Medium',
 '{tier} customer, {severity} issue, SLA {sla}.',
 'Issue has no owner — assigning one is the next concrete step.'),

('tool:fallback_followup',             'recommend_next_action_tool', 999,
 '{}'::jsonb,
 'CUSTOMER_FOLLOW_UP', 'Medium',
 '{tier} customer, {severity} issue, SLA {sla}.',
 'Default — nothing else matched, follow up with the customer.')
ON CONFLICT (rule_ref) DO NOTHING;

-- customer_escalation_summary skill (src/acme_app/skills/customer_escalation_summary.py:31)
INSERT INTO action_recommendation_rules
    (rule_ref, recommender, priority_order, conditions, action_type, recommended_priority, rationale_template, notes)
VALUES
('skill:ces:critical',                 'customer_escalation_summary', 10,
 '{"risk":"Critical"}'::jsonb,
 'PREPARE_RECOVERY_PLAN', 'Critical',
 NULL,
 'Critical risk → recovery plan, regardless of other facts.'),

('skill:ces:high_p1',                  'customer_escalation_summary', 20,
 '{"risk":"High","severity":"P1"}'::jsonb,
 'PREPARE_RECOVERY_PLAN', 'High',
 NULL,
 'High risk on a P1 — still warrants a recovery plan, not just escalation.'),

('skill:ces:high_other',               'customer_escalation_summary', 30,
 '{"risk":"High"}'::jsonb,
 'ESCALATE_ISSUE', 'High',
 NULL,
 'High risk on a lower-severity issue — escalate.'),

('skill:ces:medium_no_owner',          'customer_escalation_summary', 40,
 '{"risk":"Medium","has_owner":false}'::jsonb,
 'ASSIGN_OWNER', 'Medium',
 NULL,
 'Medium risk and no owner — assign one.'),

('skill:ces:medium_owned',             'customer_escalation_summary', 50,
 '{"risk":"Medium"}'::jsonb,
 'CUSTOMER_FOLLOW_UP', 'Medium',
 NULL,
 'Medium risk with an owner already — schedule a follow-up.'),

('skill:ces:fallback_review',          'customer_escalation_summary', 999,
 '{}'::jsonb,
 'SCHEDULE_REVIEW', 'Low',
 NULL,
 'Default — low risk or unclassified, just keep an eye on it.')
ON CONFLICT (rule_ref) DO NOTHING;

-- closure_readiness_check skill (src/acme_app/skills/closure_readiness_check.py)
INSERT INTO action_recommendation_rules
    (rule_ref, recommender, priority_order, conditions, action_type, recommended_priority, rationale_template, notes)
VALUES
('skill:crc:ready',                    'closure_readiness_check', 10,
 '{"ready_to_close":true}'::jsonb,
 'UPDATE_ISSUE_STATUS', 'Medium',
 'All closure conditions satisfied.',
 'Issue is ready to close — propose the status update.'),

('skill:crc:fallback_request_info',    'closure_readiness_check', 999,
 '{}'::jsonb,
 'REQUEST_MISSING_INFO', 'High',
 NULL,  -- skill composes a dynamic rationale from the missing-info list
 'Default — request the missing closure artefacts.')
ON CONFLICT (rule_ref) DO NOTHING;
