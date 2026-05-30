-- Migration for DECISION_LOG D-017.
-- Idempotent: safe to re-run.
-- For dev wipes, prefer `docker compose down -v` instead — this migration
-- exists for already-running deployments.

-- ─── user_roles: add lifecycle columns ──────────────────────────────────────
ALTER TABLE user_roles ADD COLUMN IF NOT EXISTS is_active   BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE user_roles ADD COLUMN IF NOT EXISTS revoked_at  TIMESTAMPTZ;
ALTER TABLE user_roles ADD COLUMN IF NOT EXISTS revoked_by  TEXT;

-- ─── Eval-persona users (idempotent) ────────────────────────────────────────
INSERT INTO users (username, email, display_name, is_active) VALUES
('eval.sales',   'eval.sales@example.local',   'Eval Sales Persona',   false),
('eval.support', 'eval.support@example.local', 'Eval Support Persona', false),
('eval.admin',   'eval.admin@example.local',   'Eval Admin Persona',   false)
ON CONFLICT (username) DO NOTHING;

INSERT INTO user_roles (user_id, role_name, granted_by)
SELECT u.id, 'sales_user', 'seed-eval' FROM users u WHERE u.username='eval.sales'
ON CONFLICT DO NOTHING;
INSERT INTO user_roles (user_id, role_name, granted_by)
SELECT u.id, 'support_user', 'seed-eval' FROM users u WHERE u.username='eval.support'
ON CONFLICT DO NOTHING;
INSERT INTO user_roles (user_id, role_name, granted_by)
SELECT u.id, 'admin', 'seed-eval' FROM users u WHERE u.username='eval.admin'
ON CONFLICT DO NOTHING;

-- ─── Live FK columns alongside snapshot text ────────────────────────────────
ALTER TABLE conversations  ADD COLUMN IF NOT EXISTS user_id            UUID REFERENCES users(id);
ALTER TABLE agent_traces   ADD COLUMN IF NOT EXISTS user_id            UUID REFERENCES users(id);
ALTER TABLE next_actions   ADD COLUMN IF NOT EXISTS created_by_user_id UUID REFERENCES users(id);
ALTER TABLE eval_results   ADD COLUMN IF NOT EXISTS user_id            UUID REFERENCES users(id);

-- Backfill the new FKs from the existing snapshot text columns. Rows where
-- the snapshot doesn't map to any user (e.g. an external "eng.team") stay
-- with user_id = NULL — that's the intended semantics.
UPDATE conversations c
   SET user_id = u.id
  FROM users u
 WHERE c.user_id IS NULL AND u.username = c.username;

UPDATE agent_traces a
   SET user_id = u.id
  FROM users u
 WHERE a.user_id IS NULL AND u.username = a.username;

UPDATE next_actions n
   SET created_by_user_id = u.id
  FROM users u
 WHERE n.created_by_user_id IS NULL AND u.username = n.created_by;

UPDATE eval_results e
   SET user_id = u.id
  FROM users u
 WHERE e.user_id IS NULL
   AND u.username = CASE e.role_name
                      WHEN 'sales_user'   THEN 'eval.sales'
                      WHEN 'support_user' THEN 'eval.support'
                      WHEN 'admin'        THEN 'eval.admin'
                    END;

-- ─── Active-row views ───────────────────────────────────────────────────────
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
    SELECT * FROM customers WHERE status = 'active';

CREATE OR REPLACE VIEW v_active_conversations AS
    SELECT * FROM conversations WHERE deleted_at IS NULL;

-- ─── GDPR Article 17 erasure path ───────────────────────────────────────────
CREATE OR REPLACE FUNCTION redact_user_pii(target_user_id UUID)
RETURNS TABLE(users_redacted INT, traces_redacted INT) AS $$
DECLARE
    target_username TEXT;
    u_count INT := 0;
    t_count INT := 0;
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

    users_redacted := u_count;
    traces_redacted := t_count;
    RETURN NEXT;
END;
$$ LANGUAGE plpgsql;
