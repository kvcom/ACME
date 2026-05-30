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
