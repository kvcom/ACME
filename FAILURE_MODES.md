# Failure Modes

How the system degrades when each dependency misbehaves. Eval case 13 covers the LLM path; the rest are documented and (where practical) verified by the `/ready` endpoint.

| Dependency | Symptom | System behaviour | User-visible message |
|---|---|---|---|
| LLM API | Timeout / 429 / 5xx | The selected provider raises a typed failure; the orchestrator records an LLM-unavailable trace and does not invent grounded facts. Auto routes may try the configured next real provider before surfacing the error. | *"The assistant is temporarily unable to answer. Trace TRC-... recorded."* |
| LLM API | Missing API key | Provider constructors fail fast with a clear configuration error. The app does not silently switch to a mock model. | The user sees the LLM-unavailable message and the trace captures the failed provider path. |
| Keycloak | Down at startup | App still starts. `/login` falls back to demo cookie session for the three demo users (DECISION_LOG D-005). | Login still works for demo users; production users see *"Authentication unavailable, please retry."* |
| Keycloak | Down mid-session | JWT decoding still works (we decode without verification — D-004); expired tokens cause 401. | *"Session expired. Please sign in again."* |
| PostgreSQL | Down | All endpoints return 503. No fallback. Healthcheck flags red. | *"The system is unavailable. Please try again shortly."* |
| Redis | Down | Chat still works for fresh queries. Follow-up references like *"that action"* return a clarification request because pending_action cannot be read. Tests have an in-memory fallback for unit testability. | *"I can answer about a specific customer or issue — please name it directly."* |
| MCP server | Down | Tool calls fail individually via `MCPClient.MCPClientError`. The orchestrator records `tool_call_log` with status=error and continues with whatever facts are available, but writes will not happen. | *"I couldn't reach one of my data sources (get_open_issues). Trace TRC-…"* |
| MCP tool | Returns malformed output | The MCP server's Pydantic schemas reject the input before any SQL runs. Bad output from SQL would surface as a tool error and be recorded as such. | Same as above. |
| OpenTelemetry collector | Down | Traces, metrics and logs are dropped by the SDK/exporters. trace_events / agent_traces are still persisted to PostgreSQL because the custom trace viewer is independent of the OTel pipeline. | (No user-visible effect.) |
| Jaeger / Prometheus / Grafana | Down | Operational trace or metric UIs are unavailable, but the app and Decision Ledger continue. The trace popover still reconstructs spans from PostgreSQL and Jaeger links can be copied/retried later. | (No user-visible effect inside the assistant.) |
| Idempotency collision | Same `idempotency_key` confirmed twice | MCP `create_next_action` returns `{created: false, duplicate: true, existing_action_ref}` on the second attempt; no second row is written. | *"Action already exists (NA-…); no duplicate was created."* |
| Confirmation token expired | Token older than 600 s | `verify_confirmation_token` returns expired; the API returns 403; the user is shown a fresh proposed-action card on re-asking. | *"This proposal has expired. Please re-issue the request."* |
| Adversarial input | Prompt injection attempt | `adversarial.check` flags; planner returns refusal; no tools called; no RBAC bypass. Trace shows `adversarial_block` event. | *"I can't follow that instruction…"* (full text in orchestrator.) |

## Verifying

- `GET /ready` reports per-dependency status. Use it as the basis for ops dashboards.
- `make eval` includes case 13 (LLM unavailable) and the adversarial / idempotency cases.
- `tests/test_failure_modes.py` covers missing-key, unknown-provider, and LLM-unavailable behaviour.
- `tests/test_idempotency.py` covers token integrity and key stability.

## What is intentionally not handled

- **Network partition between app and MCP under load** — no circuit breaker yet; documented as a production hardening item.
- **Keycloak realm key rotation** — JWT signature verification is off (D-004); rotation has no effect on the demo.
- **PostgreSQL failover** — single instance; no read replica. Acceptable for an MVP.
- **Streaming back-pressure** — SSE queue is unbounded per request. A pathological tool that produces thousands of events would balloon memory.
