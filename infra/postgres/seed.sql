INSERT INTO customers (name, industry, tier, region, customer_timezone, account_owner)
VALUES
('Northwind Energy', 'Energy', 'Enterprise', 'UK', 'Europe/London', 'Sarah Sales'),
('Contoso Retail', 'Retail', 'Mid-market', 'UK', 'Europe/London', 'Sarah Sales'),
('Acme Logistics Europe', 'Logistics', 'Enterprise', 'Netherlands', 'Europe/Amsterdam', 'Sam Support'),
('Acme Manufacturing Group', 'Manufacturing', 'Enterprise', 'Germany', 'Europe/Berlin', 'Sam Support'),
('BlueRiver Health', 'Healthcare', 'Strategic', 'UK', 'Europe/London', 'Admin'),
('Skyline Aviation', 'Aerospace', 'Enterprise', 'France', 'Europe/Paris', 'Sam Support')
ON CONFLICT DO NOTHING;

INSERT INTO action_catalogue (action_type, label, description, allowed_roles, required_fields, side_effect_level)
VALUES
('ASSIGN_OWNER','Assign Owner','Assign an owner',ARRAY['support_user','admin'],ARRAY['owner_name'],'medium'),
('REQUEST_MISSING_INFO','Request Missing Info','Ask for missing details',ARRAY['support_user','admin'],ARRAY['description'],'low'),
('CUSTOMER_FOLLOW_UP','Customer Follow Up','Follow up with customer',ARRAY['support_user','admin'],ARRAY['due_at'],'low'),
('ESCALATE_ISSUE','Escalate Issue','Escalate issue to management',ARRAY['support_user','admin'],ARRAY['issue_ref'],'high'),
('PREPARE_RECOVERY_PLAN','Prepare Recovery Plan','Prepare plan with owners and timeline',ARRAY['support_user','admin'],ARRAY['due_at'],'high'),
('SCHEDULE_REVIEW','Schedule Review','Schedule review meeting',ARRAY['support_user','admin'],ARRAY['due_at'],'low'),
('UPDATE_ISSUE_STATUS','Update Issue Status','Update issue status',ARRAY['support_user','admin'],ARRAY['new_status'],'medium'),
('CREATE_EXEC_SUMMARY','Create Executive Summary','Create management summary',ARRAY['admin'],ARRAY['description'],'low')
ON CONFLICT DO NOTHING;

INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-102', c.id, 'API integration delay', 'Northwind API integration is delayed', 'P1', 'Open', 'Breached', 'Sam Support', now() - interval '10 days'
FROM customers c WHERE c.name='Northwind Energy'
ON CONFLICT DO NOTHING;

INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-501', c.id, 'Maintenance scheduling drift', 'Maintenance scheduler has drift', 'P2', 'Open', 'At Risk', 'Sam Support', now() - interval '6 days'
FROM customers c WHERE c.name='Skyline Aviation'
ON CONFLICT DO NOTHING;

INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Customer requested written recovery plan by Friday.', 'customer_update', 'sam.support'
FROM issues i WHERE i.issue_ref='ISS-102';
