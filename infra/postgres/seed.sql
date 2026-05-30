-- Users (mirror of the Keycloak `acme` realm; see DECISION_LOG D-016).
-- Postgres is the source of truth for role assignment; Keycloak only
-- authenticates the bearer.
INSERT INTO users (username, email, display_name) VALUES
('sarah.sales',  'sarah.sales@example.local',  'Sarah Sales'),
('sam.support',  'sam.support@example.local',  'Sam Support'),
('admin.acme',   'admin.acme@example.local',   'Admin Acme')
ON CONFLICT (username) DO NOTHING;

-- Eval-persona users (D-017): permanent rows that connect the eval island
-- (eval_runs, eval_results) to the identity graph. They never log in;
-- is_active=false hides them from user pickers but lets eval_results.user_id
-- carry a valid FK so the ER diagram has no orphans.
INSERT INTO users (username, email, display_name, is_active) VALUES
('eval.sales',   'eval.sales@example.local',   'Eval Sales Persona',   false),
('eval.support', 'eval.support@example.local', 'Eval Support Persona', false),
('eval.admin',   'eval.admin@example.local',   'Eval Admin Persona',   false)
ON CONFLICT (username) DO NOTHING;

INSERT INTO user_roles (user_id, role_name, granted_by)
SELECT u.id, 'sales_user', 'seed-eval'
FROM users u WHERE u.username = 'eval.sales'
ON CONFLICT DO NOTHING;
INSERT INTO user_roles (user_id, role_name, granted_by)
SELECT u.id, 'support_user', 'seed-eval'
FROM users u WHERE u.username = 'eval.support'
ON CONFLICT DO NOTHING;
INSERT INTO user_roles (user_id, role_name, granted_by)
SELECT u.id, 'admin', 'seed-eval'
FROM users u WHERE u.username = 'eval.admin'
ON CONFLICT DO NOTHING;

INSERT INTO user_roles (user_id, role_name, granted_by)
SELECT u.id, 'sales_user', 'seed'
FROM users u WHERE u.username = 'sarah.sales'
ON CONFLICT DO NOTHING;

INSERT INTO user_roles (user_id, role_name, granted_by)
SELECT u.id, 'support_user', 'seed'
FROM users u WHERE u.username = 'sam.support'
ON CONFLICT DO NOTHING;

INSERT INTO user_roles (user_id, role_name, granted_by)
SELECT u.id, 'admin', 'seed'
FROM users u WHERE u.username = 'admin.acme'
ON CONFLICT DO NOTHING;

-- Customers
INSERT INTO customers (name, industry, tier, region, customer_timezone, account_owner) VALUES
('Northwind Energy', 'Energy', 'Enterprise', 'UK', 'Europe/London', 'Sarah Sales'),
('Contoso Retail', 'Retail', 'Mid-market', 'UK', 'Europe/London', 'Sarah Sales'),
('Acme Logistics Europe', 'Logistics', 'Enterprise', 'Netherlands', 'Europe/Amsterdam', 'Sam Support'),
('Acme Manufacturing Group', 'Manufacturing', 'Enterprise', 'Germany', 'Europe/Berlin', 'Sam Support'),
('BlueRiver Health', 'Healthcare', 'Strategic', 'UK', 'Europe/London', 'Admin'),
('Skyline Aviation', 'Aerospace', 'Enterprise', 'France', 'Europe/Paris', 'Sam Support')
ON CONFLICT DO NOTHING;

-- Action catalogue
INSERT INTO action_catalogue (action_type, label, description, allowed_roles, required_fields, side_effect_level) VALUES
('ASSIGN_OWNER','Assign Owner','Assign an owner to an issue',ARRAY['support_user','admin'],ARRAY['owner_name'],'medium'),
('REQUEST_MISSING_INFO','Request Missing Info','Ask the customer for missing details',ARRAY['support_user','admin'],ARRAY['description'],'low'),
('CUSTOMER_FOLLOW_UP','Customer Follow Up','Follow up with the customer',ARRAY['support_user','admin'],ARRAY['due_at'],'low'),
('ESCALATE_ISSUE','Escalate Issue','Escalate the issue to management',ARRAY['support_user','admin'],ARRAY['issue_ref'],'high'),
('PREPARE_RECOVERY_PLAN','Prepare Recovery Plan','Prepare written recovery plan',ARRAY['support_user','admin'],ARRAY['due_at'],'high'),
('SCHEDULE_REVIEW','Schedule Review','Schedule a review meeting',ARRAY['support_user','admin'],ARRAY['due_at'],'low'),
('UPDATE_ISSUE_STATUS','Update Issue Status','Update the status of an issue',ARRAY['support_user','admin'],ARRAY['new_status'],'medium'),
('CREATE_EXEC_SUMMARY','Create Executive Summary','Produce a management summary',ARRAY['admin'],ARRAY['description'],'low')
ON CONFLICT DO NOTHING;

-- Northwind issues
INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-102', c.id, 'API integration delay', 'Authentication timeout and intermittent token refresh failure during onboarding.', 'P1', 'Open', 'Breached', 'Sam Support', now() - interval '10 days'
FROM customers c WHERE c.name='Northwind Energy' ON CONFLICT DO NOTHING;

INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-107', c.id, 'Billing reference query', 'Customer asked for confirmation of billing reference.', 'P3', 'Waiting for Customer', 'Within SLA', 'Sarah Sales', now() - interval '3 days'
FROM customers c WHERE c.name='Northwind Energy' ON CONFLICT DO NOTHING;

-- Contoso Retail issues
INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-204', c.id, 'Delayed onboarding configuration', 'Onboarding configuration steps blocked by missing tenant detail.', 'P2', 'In Progress', 'At Risk', 'Sam Support', now() - interval '7 days'
FROM customers c WHERE c.name='Contoso Retail' ON CONFLICT DO NOTHING;

INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-209', c.id, 'Duplicate contact records', 'Two contact records exist for the same primary user.', 'P3', 'Open', 'Within SLA', 'Sarah Sales', now() - interval '2 days'
FROM customers c WHERE c.name='Contoso Retail' ON CONFLICT DO NOTHING;

-- Acme Logistics Europe
INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-301', c.id, 'Warehouse notification failures', 'Outbound notifications to warehouse partners failing intermittently.', 'P2', 'Open', 'At Risk', 'Sam Support', now() - interval '5 days'
FROM customers c WHERE c.name='Acme Logistics Europe' ON CONFLICT DO NOTHING;

-- Acme Manufacturing Group
INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-401', c.id, 'Integration credentials expired', 'Production integration credentials expired without rotation.', 'P1', 'Escalated', 'Breached', 'Sam Support', now() - interval '12 days'
FROM customers c WHERE c.name='Acme Manufacturing Group' ON CONFLICT DO NOTHING;

-- Skyline Aviation (borderline)
INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-501', c.id, 'Maintenance scheduling drift', 'Maintenance scheduler is drifting from agreed cadence.', 'P2', 'Open', 'At Risk', 'Sam Support', now() - interval '6 days'
FROM customers c WHERE c.name='Skyline Aviation' ON CONFLICT DO NOTHING;

INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-502', c.id, 'Reporting dashboard slowness', 'Reporting dashboard has noticeably increased latency.', 'P3', 'In Progress', 'Within SLA', 'Sam Support', now() - interval '4 days'
FROM customers c WHERE c.name='Skyline Aviation' ON CONFLICT DO NOTHING;

-- ISS-102 updates
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Customer reported authentication timeout during onboarding.', 'customer_update', 'sarah.sales' FROM issues i WHERE i.issue_ref='ISS-102';
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Engineering reproduced intermittent token refresh failure.', 'engineering_update', 'eng.team' FROM issues i WHERE i.issue_ref='ISS-102';
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Temporary workaround shared, customer says it is not production-ready.', 'engineering_update', 'eng.team' FROM issues i WHERE i.issue_ref='ISS-102';
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Customer requested written recovery plan by Friday.', 'customer_update', 'sam.support' FROM issues i WHERE i.issue_ref='ISS-102';
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'No confirmed resolution date has been recorded.', 'internal_note', 'sam.support' FROM issues i WHERE i.issue_ref='ISS-102';

-- ISS-301 updates
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Partner reported missing dispatch confirmations.', 'customer_update', 'partner.ops' FROM issues i WHERE i.issue_ref='ISS-301';
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Network issue between provider and warehouse identified.', 'engineering_update', 'eng.team' FROM issues i WHERE i.issue_ref='ISS-301';
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'No follow-up since Tuesday; awaiting owner assignment.', 'internal_note', 'sam.support' FROM issues i WHERE i.issue_ref='ISS-301';

-- ISS-401 updates
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Credentials expired in production; integration offline.', 'engineering_update', 'eng.team' FROM issues i WHERE i.issue_ref='ISS-401';
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Customer escalated through account team; senior attention required.', 'customer_update', 'sam.support' FROM issues i WHERE i.issue_ref='ISS-401';

-- ISS-501 updates
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Scheduler drift first noticed by maintenance lead.', 'customer_update', 'cust.ops' FROM issues i WHERE i.issue_ref='ISS-501';
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Two consecutive days with no engineering update.', 'internal_note', 'sam.support' FROM issues i WHERE i.issue_ref='ISS-501';

-- ISS-204 updates
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Customer waiting on configuration steps from onboarding team.', 'customer_update', 'sarah.sales' FROM issues i WHERE i.issue_ref='ISS-204';
INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Owner assignment unclear; pending discussion in sync.', 'internal_note', 'sam.support' FROM issues i WHERE i.issue_ref='ISS-204';
