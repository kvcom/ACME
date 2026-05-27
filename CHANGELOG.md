# Changelog

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
- LLM provider abstraction: Stub (default, deterministic), Anthropic, OpenAI, Ollama (failure-mode stub)
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
- AI_USAGE.md with prompts/ folder pointers
