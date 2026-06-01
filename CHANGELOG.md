# Changelog

## 1.1.0 — As-built features & hardening

Built on the 1.0.0 foundation; see DECISION_LOG D-016 through D-022 for rationale.

### Authorization & identity
- Postgres is the authorization source of truth: Keycloak authenticates, `users`/`user_roles` decide roles (D-016). First-login links the Keycloak subject.
- Append-only data model: no DELETE path; lifecycle via columns; live `user_id` FKs alongside historical text snapshots; `redact_user_pii()` for GDPR erasure (D-017).

### Agent behaviour as configuration
- Data-driven `action_catalogue` with hot-reload over `LISTEN/NOTIFY`; the planner prompt is built from the live set (D-019).
- Data-driven `action_recommendation_rules` engine; the former hard-coded decision trees became seed rows (D-020).
- Local-model contribution to the adversarial and PII-redaction stages (D-016 era).

### Admin & observability surfaces
- Realtime admin **DB Explorer**: pivot drill-down across FK/reverse-FK links, live row pushes over WebSocket from Postgres triggers, and validated append/in-place edit for a curated table set (audit tables stay read-only) (D-018, D-021).
- OpenTelemetry exported to Jaeger (traces) and Prometheus/Grafana (metrics); the Decision Ledger remains the audit source of truth.

### Security & requirement-gap remediation (self-audit, D-022)
- Session cookie is now HMAC-signed and tamper-evident (was base64-only and forgeable).
- Bearer JWTs are verified against the realm JWKS (RS256), on by default (was decoded without verification).
- Propose-confirm now dispatches the correct write tool per proposal — `create_next_action`, `update_issue_status`, or `update_next_action` — via a single shared router; `UPDATE_NEXT_ACTION` added to the catalogue.
- Confirmation tokens are bound to the acted-on issue/action, blocking cross-resource replay.
- DB Explorer WebSocket re-authorizes mid-session (cookie + live DB role) so privilege revocation takes effect without reconnect.
- Default placeholder secrets fail closed outside dev (`_guard_secrets`).
- `redact_user_pii()` extended to authored free text (`issue_updates.update_text`, `next_actions.description`).

### Tests
- Suite expanded to 158 passing (4 integration-skipped), including cookie-tamper, JWT, propose-confirm dispatch, and confirmation-token binding cases.

## 1.0.0 — Initial submission

Built end-to-end per [plan_v2.md](plan_v2.md):

### Infrastructure
- Docker Compose with app, postgres, redis, keycloak, mcp-server, otel-collector
- Postgres init.sql with 13 tables; seed.sql with 6 customers and 9 issues
- Keycloak realm with three demo users and three roles
- `.env.example` with provider, MCP, Keycloak, HMAC secret configuration

### Backend
- FastAPI app with src-layout package discovery
- Auth via Keycloak direct grant with demo-cookie fallback
- Domain layer (Customer, Issue, Action, RiskAssessment) — mypy --strict
- Policy layer (RBAC matrix, ActionCatalogue, ActionGuard, PII redactor, HMAC token mint/verify)
- Skills: customer_escalation_summary v1, closure_readiness_check v1
- LLM provider abstraction: Anthropic, OpenAI, Google Gemini, local Ollama, plus an Auto router; a real provider is the default and provider failures surface as explicit LLM-unavailable traces (no hidden mock)
- Cost table per provider; per-request cost/tokens/latency tracking
- Custom MCP server with 8 tools, Pydantic input validation, HMAC token verification, idempotency
- Agent orchestrator with adversarial check, PII redaction, dynamic tool selection, propose-confirm flow
- SSE streaming chat endpoint
- OpenTelemetry span emission; decision_ledger persists trace_events/tool_call_logs/rbac_decisions to Postgres

### UI
- Three-panel chat with sidebar (conversations, provider switcher), centre (streaming thread), right (evidence, proposed-action card, trace summary)
- Login with demo-user quick buttons
- Trace list and trace detail with Evidence-to-Action Decision Graph SVG
- Eval results viewer

### Evaluation
- 13 cases × 3 runs, five scoring axes, variance reporting
- EVAL_RESULTS.md regenerated on each `make eval`

### Tests
- 65 passing unit tests across auth, RBAC, adversarial, PII, skills, agent planning, idempotency, propose-confirm, MCP schemas, traces, eval scoring, failure modes
- Integration markers for tests that require live services

### Documentation
- README with design lineage paragraph and demo workflows
- ARCHITECTURE.md with module map, sequence diagrams, state model
- FAILURE_MODES.md with per-dependency matrix
- DECISION_LOG.md with 14 entries
- AI_USAGE.md documenting tools, workflow and review process
