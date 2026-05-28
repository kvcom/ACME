CREATE EXTENSION IF NOT EXISTS pgcrypto;

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
