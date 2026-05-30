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

**Choice**: `/login` first tries Keycloak; on transport failure it falls back to a small hard-coded demo user table and issues a short-lived demo cookie session with the same role envelope.

**Why**: Demos fail when local services flake. A fallback that uses only the three pre-defined, non-secret demo users keeps the panel demo robust without weakening the server-side RBAC story. The UI labels these as demo identities, and the fallback is intentionally documented here as a prototype trade-off.

**Assurance**: The authenticated user carries an `auth_source` value that is surfaced in the UI header and `/auth/me`. Wrong credentials stop the login; only Keycloak transport/unavailability or local imported-user setup issues can trigger the fallback. `DEMO_AUTH_FALLBACK_ENABLED=false` disables the fallback entirely for live-Keycloak demonstrations.

**Production**: Remove the fallback entirely. Production users authenticate exclusively through Keycloak or an enterprise IdP; demo users are seeded in the IdP for test environments only, never as application code credentials.

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


## D-016 · Postgres is the authorization source of truth; Keycloak only authenticates

**Choice**: The schema includes `users` and `user_roles` tables. Keycloak still verifies the password and issues the JWT, but at login time we look up the user in Postgres and the role list on the resulting `CurrentUser` comes from `user_roles`, not from the token's `realm_access.roles`.

**Why**: The brief (§4.6) lists `users or user_roles` as a required table. Two options were considered:
- A. Keep Keycloak as the sole source of roles, add a read-only mirror table in Postgres for documentation.
- B. Make Postgres the source of truth for roles; demote Keycloak to authentication only.

B was chosen. It removes the single-source-of-truth ambiguity (with A, two systems can disagree and the answer to "which roles does Sarah have?" depends on who you ask). It also lets this app own its own role vocabulary — Keycloak realm roles and app roles no longer have to match name-for-name forever.

**Implementation**:
- DDL: `users(id, username UNIQUE, email, display_name, keycloak_subject, is_active, created_at, deleted_at)` and `user_roles(id, user_id FK, role_name CHECK IN ('sales_user','support_user','admin'), granted_at, granted_by)`.
- Seed: three demo users (sarah.sales / sam.support / admin.acme) with one role each, matching the Keycloak realm.
- Login (`/auth/login`, `POST /login`): Keycloak verifies the password → Postgres lookup via `get_roles_for_username` → if the user is missing or has no supported role, login is rejected even if Keycloak said yes. The resolved role list is then encoded into the signed session cookie.
- No per-request DB hit: the session cookie carries the role snapshot, consistent with how JWT systems work. Trade-off: revoking a role takes effect on next login.

**Production**:
- Add a short-TTL Redis cache and a SIGHUP-style cookie-invalidation channel so revocations propagate within seconds.
- Promote `granted_by` to a foreign key to `users.id` and add a `user_role_audit` table that captures grant/revoke events.
- Replace direct grant with Authorization Code + PKCE (D-003) but keep this same role-resolution rule on the callback.


## D-017 · Append-only database with universal soft-delete and a connected ER graph

**Choice**: The application never emits `DELETE`. Lifecycle changes are expressed by updating an existing column on the row — entity-specific where one already exists (`customers.status`, `issues.status`, `next_actions.status`, `conversations.deleted_at`), generic `is_active boolean` where one didn't. Once "rows never leave" is a global invariant, every `username` / actor column in the audit and business graph also carries a real `user_id` FK into `users(id)`, so the ER diagram becomes a single connected graph with no orphan islands except the eval harness (and even that gains FK links via three permanent eval-persona users).

**Why**: The Decision Ledger principle (plan_v2 §2.5) and D-015 (soft-delete of conversations) were already pulling in this direction per-table. Universalising the rule simplifies reasoning ("nothing ever leaves") and unlocks proper FKs everywhere without the previous fear that a user deletion would cascade-destroy the audit trail.

**Rules of the model**:
- `INSERT` and `UPDATE` are the only DML the application emits against business or identity tables.
- `DELETE` is forbidden in repositories — audit tables additionally have UPDATE forbidden so history can never be rewritten (immutability via Postgres role privilege, not policy hope).
- **Corrections** (typos, data fixes) are UPDATEs on the live row.
- **End-of-life** sets the existing lifecycle column (`status`, `deleted_at`) or `is_active=false` for tables without a richer lifecycle.
- The only way rows actually leave the database is a dev-time `docker compose down -v` — outside the app's reach.

**Implementation**:
- **Lifecycle columns**: kept where they encode domain meaning (`customers.status`, `issues.status`, `next_actions.status`, `next_actions.completed_at`, `conversations.deleted_at`, `users.is_active`/`deleted_at`, `action_catalogue.is_active`). Added `user_roles.is_active` because that was the one identity-side table without a deactivation path.
- **FK columns** added alongside (not replacing) the snapshot text columns:
  - `conversations.user_id` → `users(id)`
  - `agent_traces.user_id` → `users(id)`
  - `next_actions.created_by_user_id` → `users(id)`
  - `eval_results.user_id` → `users(id)` (via three new eval-persona users `eval.sales` / `eval.support` / `eval.admin`, seeded `is_active=false` so they don't appear in user pickers)
- **TEXT snapshots stay**: `agent_traces.username`, `rbac_decisions.username`, etc. The FK is the live join; the TEXT is the historical display value frozen at write time. If a user's `display_name` changes later, old traces still read what the user actually was called.
- **Active-row views** (SQL views, so the filter is impossible to forget):
  - `v_active_users` — `users` with `is_active=true AND deleted_at IS NULL`
  - `v_active_user_roles` — `user_roles` with `is_active=true`
  - `v_active_customers` — `customers` with `status = 'active'`
  - `v_active_conversations` — `conversations` with `deleted_at IS NULL`
  - Listing endpoints read from views; audit/trace endpoints read from base tables.
- **GDPR Article 17 erasure path**: stored function `redact_user_pii(user_id)` overwrites `users.email`, `users.display_name`, and the raw `agent_traces.user_query` for that user's traces with the literal token `[REDACTED-GDPR]`. The row count stays the same; the redaction itself is logged. Builds on the at-ingest PII redactor (D-012) — `user_query_redacted` already had the regex-targeted PII removed at write time, so the residual privacy risk is the raw `user_query` column, and the function blanks that.
- **Display-string actor columns** (`customers.account_owner`, `issues.owner`, `next_actions.owner_name`, `issue_updates.created_by`) remain TEXT for now. They can legitimately hold non-system actors ("eng.team", "partner.ops", external owners). Promoting them to FKs requires a separate "staff/personnel" model decision and is deferred — they are not blockers for the no-orphan goal because the *current-user* and *creator* FKs above already link the audit and business graphs to identity.
- **Storage policy** (not yet implemented, captured here so the choice is visible): partition `agent_traces` and `trace_events` by month; archive partitions older than two years to cold object storage. The hot DB stays small even though "nothing ever leaves".

**Production**:
- Replace redaction-in-place with **crypto-shredding**: encrypt PII columns with a per-user key, drop the key on erasure. Row count unchanged, PII becomes unrecoverable ciphertext, no need to overwrite text.
- Add the storage-partitioning + cold-archive job. Audit tables get bigger forever otherwise.
- Tighten the FK story for ownership: introduce a `staff` table covering both system users and named non-user actors, and promote the display-string columns to FKs against it.
- Enforce immutability at the DB role level: the application's DB role should have `INSERT, UPDATE` on identity/business tables, `INSERT only` on audit tables, and `DELETE` on nothing. The dev "wipe" runs as a different role.


## D-018 · Realtime DB Explorer via Postgres LISTEN/NOTIFY → WebSocket

**Choice**: The admin DB Explorer at `/db-explorer` updates in real time. Postgres triggers on every explorable table emit `pg_notify('db_explorer', {table, op, id})` after INSERT/UPDATE. The app holds a dedicated asyncpg connection that `LISTEN`s on that channel and fans incoming notifications out to admin WebSocket connections at `/db-explorer/ws`. The browser receives the event, fetches the full row via `GET /db-explorer/row/{table}/{id}`, and surgically inserts or updates the row in place with a brief highlight animation. Open nested drill-down groups whose target table changed are marked as stale; clicking re-fetches and re-renders that group in place.

**Why** (vs. polling, vs. SSE, vs. WebSocket): Polling at 3s intervals would have been adequate for current scale, but the user signalled that interactive write features ("edit/append from the explorer") are coming next — at that point a bidirectional channel becomes genuinely useful, and committing to WS now avoids a transport rewrite later. SSE was the runner-up — same backend story, half the client code — but the project already has multiple SSE consumers (`/chat/stream`) and ergonomics-wise both are similar; the bidirectional headroom of WS is the tiebreaker. `LISTEN/NOTIFY` was chosen over write-path instrumentation because the DB is the only authoritative point that *every* mutation passes through — adding "emit an event" at the orchestrator level would have missed direct admin writes, future bulk imports, and dev `psql` sessions.

**Implementation**:
- **Triggers** (`infra/postgres/init.sql` + migration `d018_realtime_triggers.sql`): a single `notify_db_explorer()` plpgsql function reads `id` (or `action_type` for the natural-key table) from `NEW` via `row_to_json` and calls `pg_notify`. A `DO $$` block attaches `AFTER INSERT OR UPDATE` triggers to all 14 explorable tables in one go. The payload is intentionally minimal — `{table, op, id}` — to stay well below the 8 KB `pg_notify` ceiling for wide rows like `agent_traces.user_query`; the client hydrates via REST after the push.
- **DELETE not covered**: D-017's append-only invariant means rows don't disappear from these tables, only flip lifecycle columns — which fire as UPDATE, which is covered.
- **Broadcaster** (`src/acme_app/application/realtime.py`): a single `Broadcaster` singleton holds one long-lived asyncpg connection (independent of the SQLAlchemy pool — `LISTEN` connections can't be pooled). The connection's `add_listener` callback parses each notification and dispatches via per-subscriber `asyncio.Queue`s so a slow client never stalls delivery to others. The reader loop auto-reconnects with exponential backoff (capped at 30 s) so a DB restart doesn't take live updates down permanently.
- **WebSocket route** (`/db-explorer/ws`): cookie-authenticated (`acme_session`), admin-only — closes immediately with WS code 1008 for non-admins. The protocol is symmetric JSON: `{"watch": "<table>" | null}` from client to narrow the firehose, `{table, op, id}` from server for every match. Server sends a one-shot `{"hello": ...}` on accept so the client knows the channel is live.
- **Single-row endpoint** (`/db-explorer/row/{table}/{id}`): used by the client to hydrate after receiving a push. Reuses the same column allow-list as the existing rows endpoint, so security posture is identical.
- **Frontend** (`templates/db_explorer.html`): WS client with reconnect (exponential backoff). On event, the row is fetched and either inserted at the top of the root table (new) or replaces the existing row in place (update), with a 1.6 s terracotta flash animation so the eye catches the change. Open nested groups whose target table matches the event get a `is-stale` class — header shows `· stale, click to refresh`; clicking re-fetches via `/db-explorer/related/...` using the drill coordinates stashed on the group element at render time, and replaces only the affected group's body.
- **Visual state**: header pill (`live-pill`) shows green pulsing dot when WS is connected, red dot when offline. Offline triggers reconnect immediately, then exponential backoff if it keeps failing.

**Production**:
- **Authoritative authz** on every WS event: today the WS auth check is admin-on-connect. A long-lived socket whose user later loses admin keeps receiving events until the cookie expires (8h). Add a periodic re-auth or kill the socket on a `user_roles` event for the connected user.
- **Connection ceiling**: with one admin user the broadcaster lock is negligible; at 100+ admins we'd switch the fan-out lock to a copy-on-write subscriber map.
- **Channel scoping**: today every WS receives every event (the per-socket filter is only an optimisation). At higher write volume, separate channels per table (`db_explorer.customers`, `db_explorer.agent_traces`, …) would let Postgres do the filtering, not Python.
- **Write features**: when `/db-explorer/edit-cell` (and similar) ships, it goes over the same WS — but only for confirmations / errors. The actual mutation still goes through a REST endpoint that re-validates RBAC and writes via the same orchestrator-style decision path, so the audit trail (D-017) stays intact. WebSocket is for *push and confirmation*, never for un-audited writes.
