-- D-021 demo edge cases for customer/issues/evals.
-- Safe to replay in an existing dev database.

INSERT INTO customers (name, industry, tier, region, customer_timezone, account_owner)
SELECT 'Greenfield Foods', 'Food & Beverage', 'Mid-market', 'Ireland', 'Europe/Dublin', 'Sarah Sales'
WHERE NOT EXISTS (SELECT 1 FROM customers WHERE name = 'Greenfield Foods');

INSERT INTO customers (name, industry, tier, region, customer_timezone, account_owner)
SELECT 'Redwood Telecom', 'Telecom', 'Enterprise', 'Sweden', 'Europe/Stockholm', 'Sam Support'
WHERE NOT EXISTS (SELECT 1 FROM customers WHERE name = 'Redwood Telecom');

INSERT INTO customers (name, industry, tier, region, customer_timezone, account_owner)
SELECT 'Meridian Bank', 'Financial Services', 'Strategic', 'United States', 'America/New_York', 'Admin Acme'
WHERE NOT EXISTS (SELECT 1 FROM customers WHERE name = 'Meridian Bank');

INSERT INTO customers (name, industry, tier, region, customer_timezone, account_owner)
SELECT 'Nimbus Labs', 'SaaS', 'Startup', 'United States', 'America/Los_Angeles', 'Sarah Sales'
WHERE NOT EXISTS (SELECT 1 FROM customers WHERE name = 'Nimbus Labs');

INSERT INTO customers (name, industry, tier, region, customer_timezone, account_owner)
SELECT 'Nimbus Logistics', 'Logistics', 'Mid-market', 'Canada', 'America/Toronto', 'Sam Support'
WHERE NOT EXISTS (SELECT 1 FROM customers WHERE name = 'Nimbus Logistics');

INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-701', c.id, 'Invoice export rounding error', 'Invoice export rounded tax values incorrectly for two legacy invoices.', 'P3', 'Resolved', 'Within SLA', 'Sam Support', now() - interval '11 days'
FROM customers c WHERE c.name='Greenfield Foods' ON CONFLICT DO NOTHING;

INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-801', c.id, 'Roaming provisioning backlog', 'Provisioning backlog is delaying enterprise roaming activations.', 'P2', 'Open', 'At Risk', NULL, now() - interval '9 days'
FROM customers c WHERE c.name='Redwood Telecom' ON CONFLICT DO NOTHING;

INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-802', c.id, 'Usage alert copy needs approval', 'Customer asked for revised wording on usage-alert notification copy.', 'P4', 'Waiting for Customer', 'Within SLA', 'Sarah Sales', now() - interval '1 day'
FROM customers c WHERE c.name='Redwood Telecom' ON CONFLICT DO NOTHING;

INSERT INTO issues (issue_ref, customer_id, title, description, severity, status, sla_status, owner, opened_at)
SELECT 'ISS-901', c.id, 'Payment file validation failures', 'Nightly payment files intermittently fail validation in production.', 'P1', 'Open', 'At Risk', 'Sam Support', now() - interval '2 days'
FROM customers c WHERE c.name='Meridian Bank' ON CONFLICT DO NOTHING;

INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Engineering confirmed the rounding defect is fixed in export service v2.31.', 'engineering_update', 'eng.team'
FROM issues i
WHERE i.issue_ref='ISS-701'
  AND NOT EXISTS (SELECT 1 FROM issue_updates u WHERE u.issue_id=i.id AND u.update_text='Engineering confirmed the rounding defect is fixed in export service v2.31.');

INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Customer accepted the corrected invoice exports and approved closure.', 'customer_update', 'sarah.sales'
FROM issues i
WHERE i.issue_ref='ISS-701'
  AND NOT EXISTS (SELECT 1 FROM issue_updates u WHERE u.issue_id=i.id AND u.update_text='Customer accepted the corrected invoice exports and approved closure.');

INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Provisioning backlog grew after weekend roaming batch failed.', 'customer_update', 'sam.support'
FROM issues i
WHERE i.issue_ref='ISS-801'
  AND NOT EXISTS (SELECT 1 FROM issue_updates u WHERE u.issue_id=i.id AND u.update_text='Provisioning backlog grew after weekend roaming batch failed.');

INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'No owner assigned yet; support queue is waiting for routing decision.', 'internal_note', 'sam.support'
FROM issues i
WHERE i.issue_ref='ISS-801'
  AND NOT EXISTS (SELECT 1 FROM issue_updates u WHERE u.issue_id=i.id AND u.update_text='No owner assigned yet; support queue is waiting for routing decision.');

INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Engineering asked for carrier logs before they can isolate the provisioning fault.', 'engineering_update', 'eng.team'
FROM issues i
WHERE i.issue_ref='ISS-801'
  AND NOT EXISTS (SELECT 1 FROM issue_updates u WHERE u.issue_id=i.id AND u.update_text='Engineering asked for carrier logs before they can isolate the provisioning fault.');

INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Customer warned that delayed roaming activation may affect executive travel next week.', 'customer_update', 'sam.support'
FROM issues i
WHERE i.issue_ref='ISS-801'
  AND NOT EXISTS (SELECT 1 FROM issue_updates u WHERE u.issue_id=i.id AND u.update_text='Customer warned that delayed roaming activation may affect executive travel next week.');

INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Validation failures reproduced against two payment-file batches.', 'engineering_update', 'eng.team'
FROM issues i
WHERE i.issue_ref='ISS-901'
  AND NOT EXISTS (SELECT 1 FROM issue_updates u WHERE u.issue_id=i.id AND u.update_text='Validation failures reproduced against two payment-file batches.');

INSERT INTO issue_updates (issue_id, update_text, update_type, created_by)
SELECT i.id, 'Bank operations asked for hourly status until the validation failure is resolved.', 'customer_update', 'admin.acme'
FROM issues i
WHERE i.issue_ref='ISS-901'
  AND NOT EXISTS (SELECT 1 FROM issue_updates u WHERE u.issue_id=i.id AND u.update_text='Bank operations asked for hourly status until the validation failure is resolved.');
