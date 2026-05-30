-- Migration for the DELETE-coverage gap discovered after D-019.
-- Extends the trigger function to handle DELETE (using OLD instead of NEW),
-- and reattaches every trigger with `AFTER INSERT OR UPDATE OR DELETE`.
-- Idempotent: safe to re-run.
--
-- The trigger merely *reports* the DELETE — it doesn't authorise one. The
-- append-only invariant (D-017) is enforced at the application layer
-- (repositories never emit DELETE). If an operator or admin SQL session
-- does delete a row anyway, the explorer now reflects it honestly instead
-- of going stale until the next page refresh.

CREATE OR REPLACE FUNCTION notify_db_explorer() RETURNS TRIGGER AS $$
DECLARE
    row_data JSONB;
    row_id TEXT;
BEGIN
    -- On DELETE, NEW is NULL — use OLD instead. The COALESCE pattern keeps
    -- one function body for all three operations.
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
