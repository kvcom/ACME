-- Migration for DECISION_LOG D-018 — realtime DB Explorer.
-- Idempotent: safe to re-run.
--
-- One trigger function emits a JSON notification on the `db_explorer`
-- channel after every INSERT or UPDATE on the explorable tables.
-- Payload is intentionally small ({table, op, id}) — clients fetch the
-- full row via /db-explorer/row/{table}/{id} after they receive the
-- notification, so we never bump up against pg_notify's 8 KB limit for
-- wide rows (e.g. agent_traces with a long user_query).
--
-- DELETE is intentionally NOT covered: the append-only model (D-017)
-- means no row ever leaves a business or audit table, only `is_active`
-- / `status` / `deleted_at` flips, which fire as UPDATE.

CREATE OR REPLACE FUNCTION notify_db_explorer() RETURNS TRIGGER AS $$
DECLARE
    row_id TEXT;
BEGIN
    -- All explorable tables have an `id` PK or use a natural-key TEXT PK
    -- (`action_catalogue.action_type`). The trigger function inspects NEW
    -- via row_to_json so it doesn't need to know which column to read.
    row_id := COALESCE(
        (row_to_json(NEW)::jsonb ->> 'id'),
        (row_to_json(NEW)::jsonb ->> 'action_type')
    );
    PERFORM pg_notify('db_explorer', json_build_object(
        'table', TG_TABLE_NAME,
        'op', TG_OP,
        'id', row_id
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach the trigger to every explorable table. DROP first to make
-- the migration idempotent without DROP-IF-EXISTS / CREATE-OR-REPLACE
-- syntax (which Postgres triggers don't support natively).
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
             AFTER INSERT OR UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION notify_db_explorer()',
            t
        );
    END LOOP;
END $$;
