# Decision Log

Each entry: D-NNN, the choice, why, and the production replacement.

## D-001 · PostgreSQL is the source of truth, Redis is working memory

**Choice**: All durable business state and observability live in PostgreSQL. Redis stores only conversation context, pending actions, and caches.

**Why**: If Redis is lost, the assistant loses convenience. If PostgreSQL is lost, the business record is unavailable. This separation is what makes the failure-mode matrix tractable.

**Production**: Same model; PostgreSQL clustered with read replicas; Redis replicated for HA.

## D-002 · Modular monolith over microservices

**Choice**: One FastAPI app with `domain` / `policy` / `application` / `infrastructure` / `api` module boundaries. The MCP server is a separate container only because it exposes a separate protocol surface.

**Why**: Borrowed from the Nabu One Phase 0 pattern. A five-day MVP cannot afford to fight microservice orchestration; the internal split is enough to keep the boundary clear and to make later extraction tractable.

**Production**: Extract `mcp_server`, `agent`, and `policy` into separate services only when scale or team boundaries demand it.

## D-003 · Direct-access-grant Keycloak login (not Authorization Code with PKCE)

**Choice**: Direct grant flow for the `acme-assistant` client. Single page demo login.

**Why**: Five-day delivery, single-user demo session, no real secrets, no browser-redirect interaction.

**Production**: Authorization Code with PKCE; short-lived access tokens; refresh-token rotation; cookie-bound session backed by server-side store.

## D-004 · JWT decoded without signature verification

**Choice**: `jose.jwt.get_unverified_claims` rather than full JWKS verification.

**Why**: Same as D-003. The Keycloak realm is local-only, and verifying the signature buys no security in this environment.

**Production**: Fetch realm JWKS at startup, cache, rotate. Reject tokens with unrecognised `kid` or invalid signature. Validate `aud`, `iss`, `exp`, `nbf`.

## D-005 · Demo cookie session as a fallback when Keycloak is unavailable

**Choice**: `/login` first tries Keycloak; on transport failure it falls back to a hard-coded demo user table and issues a base64-encoded session cookie with the same role envelope.

**Why**: Demos fail when third-party services flake. A fallback that uses only the three pre-defined demo users keeps the panel demo robust without weakening the realm-based RBAC story.

**Production**: Remove the fallback entirely. Production users authenticate exclusively through Keycloak.

## D-006 · Deterministic risk classification, LLM-narrated explanation

**Choice**: Risk level and recommended action type are computed by `domain/risk_rules.py` from fixed rules. The LLM only generates the narration.

**Why**: Eval correctness requires deterministic classification across runs; only wording is allowed to vary. Borrowed from the myTbot "AI advises, rules execute" pattern.

**Production**: Same model. Risk rules graduate to a configuration store with versioning and auditability.

## D-007 · HMAC-signed `confirmation_token`, no server-side token store

**Choice**: Tokens are `trace_ref|action_type|issue_ref|expires_at|hmac_sha256(secret, payload)`. The MCP server verifies them statelessly using the shared secret.

**Why**: Stateless tokens are simpler to reason about; the binding to action_type and issue_ref means a token cannot be replayed for a different action.

**Production**: Rotate the HMAC secret on schedule; consider a short-TTL Redis registry for one-time use of each token.

## D-008 · Stub LLM provider is the default; real providers opt-in

**Choice**: `LLM_PROVIDER=stub` ships as the default. Anthropic and OpenAI adapters exist and activate when their API keys are present.

**Why**: The system must run end-to-end without API keys for the panel demo. The stub planner covers all 13 eval cases deterministically and is provably correct. Real providers fall back to the stub if keys are missing so flipping the provider is non-destructive.

**Production**: Real provider primary, stub kept for unit tests and offline rehearsal.

## D-009 · Custom Acme MCP server, not generic PostgreSQL MCP

**Choice**: Eight business-level tools (`recommend_next_action`, `create_next_action`, etc.), each backed by a few SQL statements with input validation.

**Why**: The agent should consume governed capabilities, not raw database access. The same tool contracts could wrap Salesforce, ServiceNow or SAP in a real deployment.

**Production**: Same model; tools graduate to versioned schemas, deprecation policy, and per-tool circuit breakers.

## D-010 · Idempotency key = SHA-256(trace_ref || action_type || issue_ref)

**Choice**: Single idempotency rule for create_next_action. The unique constraint on `next_actions.idempotency_key` enforces it at the DB level.

**Why**: A user clicking Confirm twice should not create two actions. The trace_ref is the natural anchor — same conversation turn means same intent.

**Production**: Same model. Add an audit log entry on every duplicate hit so abuse patterns are visible.

## D-011 · Trace events persisted to PostgreSQL even if OTel collector is down

**Choice**: `trace_events` table is written from the in-process Decision Ledger regardless of OTel pipeline health.

**Why**: The custom trace viewer is the panel demo signal. Making it depend on OTel would couple a UX feature to an infrastructure component that can be down.

**Production**: Both. OTel for cross-service correlation; the ledger table for the operator-facing trace viewer.

## D-012 · PII redaction is regex-based for MVP

**Choice**: Email, card, ID, phone patterns. Production extension to Microsoft Presidio is documented but not in scope.

**Why**: Regex covers the demo data with no failure cases worth fixing in a five-day window. Adding Presidio would mean shipping a 2-GB spaCy model.

**Production**: Presidio with custom recognisers for Acme-specific identifiers; consider a separate redaction service so the same engine serves logs, traces, and exports.

## D-013 · UI is server-rendered Jinja + a single chat.js, not React

**Choice**: FastAPI templates, vanilla JS for the SSE consumer.

**Why**: A five-day demo where the UI is one of seven deliverables cannot also be a SPA build. Server-rendered HTML is fast to iterate, clearly demoable, and keeps the focus on the architecture.

**Production**: Likely React/Next; the API contract (`POST /chat`, `GET /chat/stream`, `POST /actions/confirm`) is stable enough to swap the front end without backend churn.

## D-014 · The propose-confirm flow visible at API boundary, not buried in agent logic

**Choice**: `POST /actions/confirm` is a real endpoint with HMAC verification. The agent never auto-executes a write even for admin.

**Why**: Makes "AI recommends, policy executes" visible in network traffic and trace events, not just in code comments. Borrowed from myTbot.

**Production**: Same model. Add a separate approval state machine for high-side-effect actions that require multi-step approval.

## D-015 · Conversation deletion is soft-delete only — audit trail is preserved

**Choice**: The sidebar's "delete" affordance sets `conversations.deleted_at = now()`. The underlying `agent_traces`, `trace_events`, `tool_call_logs`, `rbac_decisions` and `next_actions` rows are never touched.

**Why**: The Decision Ledger principle from plan_v2 §2.5 ("every agent action records who acted, why, on what evidence, under what permissions, with what outcome") makes those tables the durable record. Letting a user hard-delete them would mean an operator could *retroactively erase the basis on which an action was created* — which breaks the auditability story the panel hears. Soft-delete gives the user the UX affordance they expect (the chat disappears from the sidebar, doesn't clutter the list) without destroying the ledger.

**Implementation details**:
- `conversation_list` filters `WHERE deleted_at IS NULL`.
- `get_conversation_history` does **not** filter — pulling up a trace by its `trace_ref` still works even after the parent conversation is hidden, so the audit path keeps functioning.
- `soft_delete_conversation` is scoped to the conversation's `username`, so a user can only hide their own threads.
- PII redaction (D-012) already runs at write time, so the preserved trace contains the redacted query — the privacy-vs-audit balance is held by the redactor, not by deletion.

**Production**: Same model. Two additional doors:
- **Compliance hard-delete** (GDPR Article 17): admin-only endpoint that wipes a single user's traces; protected by a multi-party-approval workflow because it crosses the audit boundary. Log the deletion itself as an "audit-erasure" event so even the act of forgetting is on the ledger.
- **Retention policy**: time-based purge of `deleted_at` records older than N years — but again, *not* of the underlying traces, which graduate to cold archive.
