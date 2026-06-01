-- D-022: Security & requirement-gap hardening (self-audit remediation)
--
-- 1. GDPR: redact_user_pii() now also overwrites the free text a subject
--    authored — issue_updates.update_text and next_actions.description — not
--    just users.* and agent_traces.user_query. The return signature gains two
--    counts. CREATE OR REPLACE keeps the same name so callers are unaffected.
-- 2. action_catalogue: add UPDATE_NEXT_ACTION so admins/support can drive the
--    next_actions lifecycle (complete / cancel) through propose-confirm.
--
-- Idempotent: safe to re-run.

-- The return signature changed (added two OUT columns), so a plain CREATE OR
-- REPLACE errors on an existing function — drop it first.
DROP FUNCTION IF EXISTS redact_user_pii(uuid);

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

INSERT INTO action_catalogue
    (action_type, label, description, allowed_roles, required_fields, side_effect_level)
VALUES
    ('UPDATE_NEXT_ACTION', 'Update Next Action',
     'Mark an existing next action complete, in progress, or cancelled (cancel is admin-only)',
     ARRAY['support_user','admin'], ARRAY['action_ref','new_status'], 'medium')
ON CONFLICT (action_type) DO NOTHING;
