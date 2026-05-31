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

## D-021 · OpenTelemetry is an operational overlay with real local backends

**Choice**: Keep the Decision Ledger as the audit source of truth, but export OTel traces to Jaeger and OTel metrics to Prometheus/Grafana in the local Compose stack. Warning-and-above Python logs are also exported through the collector when the OTel SDK is available.

**Why**: The prior debug exporter proved instrumentation but discarded data. Jaeger makes the stored `otel_trace_id` actionable for engineers, while Prometheus/Grafana provide request, token, cost and latency trends without querying the audit tables.

**Boundary**: OTel remains fail-soft and non-authoritative. If the collector or backend is down, compliance/audit views still read from PostgreSQL (`agent_traces`, `trace_events`, `tool_call_logs`, `rbac_decisions`). Metrics intentionally use low-cardinality labels such as role, intent, provider, model and status; usernames, trace refs and raw user text stay out of the OTel metrics path.

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


## D-019 · `action_catalogue` is the live source of truth for action types

**Choice**: The Postgres `action_catalogue` table genuinely governs which action types exist, who can propose them, and what their required fields are. Both processes that need this knowledge (the app and the MCP server) load the catalogue from the DB and refresh on change — the app via D-018's realtime broadcaster (sub-second), the MCP server via a 5 s TTL cache. The LLM planner's system prompt is built dynamically from the loaded set so adding a new action type via the DB Explorer is effective in the *next* agent turn without a deploy.

**Why**: Before this change, the brief framed `action_catalogue` as a config table, but in practice three other layers also hardcoded the eight types — `policy/action_catalogue.py` (`CATALOGUE` dict), `mcp_server/validation.py` (`ALLOWED_ACTION_TYPES` frozenset + `ROLE_WRITE_PERMISSIONS` map), and the planner's `PLANNER_SYSTEM_PROMPT` constant. Adding a row to the DB would be silently rejected by every downstream layer. That's a four-way drift hazard, and the `is_active` column was a latent bug because nothing consulted it. Promoting the DB to actual source of truth removes the hazard, fixes the latent bug, and makes the realtime infrastructure from D-018 do useful work beyond visualisation.

**Implementation**:
- **App side (`src/acme_app/policy/action_catalogue.py`)**: replaced the static `CATALOGUE` dict with a module-level `_snapshot` populated by `refresh_from_db()`. Public API moved from constants to functions (`allowed_action_types()`, `validate_action_type()`, `role_allowed()`, `get_definition()`, `required_fields()`, `snapshot()`) so callers always observe the current state, not an import-time copy. A hardcoded bootstrap of the eight seeded types is kept and serves as a fallback if the DB is briefly unreachable at startup — stale data is better than a broken app.
- **Startup hook** (`main.py`): `refresh_from_db()` runs before the first request is accepted, then the broadcaster is started and `action_catalogue.handle_event` is registered as an internal post-fan-out handler. When any `action_catalogue` row INSERTs or UPDATEs (D-018 trigger), the handler reloads the snapshot. Verified end-to-end: inserting `REQUEST_LEGAL_REVIEW` into the live DB produced `action_catalogue loaded: 9 active types` in the app log within 2 ms.
- **Broadcaster (`application/realtime.py`)**: gained an `on_event(handler)` API and a post-fan-out invocation pass for in-process hooks. Each handler runs independently in a `try/except` so a slow or broken handler can't block WebSocket delivery or other handlers.
- **LLM planner prompt** (`infrastructure/llm/providers/anthropic_provider.py`): `PLANNER_SYSTEM_PROMPT` is now the static base. A new `planner_system_prompt()` function appends a "Registered action types (loaded live from action_catalogue)" block listing each active type with its side-effect level and allowed roles. All four providers (Anthropic, OpenAI, Google, Ollama) call the function instead of the constant. The planner is now anchored to an explicit live allow-list rather than its training-time prior on the eight names.
- **MCP side (`mcp_server/src/acme_mcp/validation.py`)**: replaced the hardcoded `ALLOWED_ACTION_TYPES` frozenset and `ROLE_WRITE_PERMISSIONS` map with a sync DB load on each call, fronted by a 5 s TTL cache. `allowed_action_types()` and `role_may_create()` are now functions that consult the cache. `role_may_create` derives permissions directly from the catalogue's `allowed_roles` TEXT[] column — no more hand-maintained role→action map. Bootstrap fallback identical to the app's, used only if the DB lookup fails or returns zero rows (defensive: never lock out every role due to a transient DB blip).
- **Tests**: `tests/test_rbac.py` updated to import `allowed_action_types()` instead of the removed `CATALOGUE` constant. Full suite: 121 passed, 4 skipped.

**Why two refresh mechanisms (app push, MCP poll)**:
- The app process already has D-018's listener — adding an internal handler costs ~10 LOC and gives sub-second propagation. Worth the complexity.
- The MCP process is intentionally narrow (psycopg sync, one HTTP request → one transaction → return). Adding a long-lived asyncpg listener thread to it would more than double its surface area. A 5 s TTL is the simpler trade: at most 5 s of staleness on a config change, with zero new infrastructure. If the staleness ever matters, the next step is to wire a tiny LISTEN consumer into MCP — it's a self-contained future task, not a redesign.

**Operator-facing contract**:
- **To add a new action type**: INSERT into `action_catalogue` (or via the future DB Explorer "append" UI). It becomes valid in the app within ~2 ms and in MCP within ≤5 s.
- **To retire one**: set `is_active = false` (UPDATE). Both processes will exclude it from `allowed_action_types()` on the next refresh. The hardcoded skills in `customer_escalation_summary.py` and `closure_readiness_check.py` still reference the eight original names — if you retire one of those, the skill will recommend a defunct action and validation will reject it cleanly with `Unknown action_type: …`. That's a deliberate fail-closed posture, but if it bothers a deployer, the skill itself needs updating.
- **Don't `DELETE`**: consistent with D-017's append-only invariant. The D-018 trigger only fires on `INSERT/UPDATE`, so a `DELETE` won't propagate to the in-memory snapshots until the next service restart or the next UPDATE on any other row. Use `is_active = false` instead.

**Remaining gap (out of scope, explicitly)**: the two skill modules that *decide which* action type to recommend in a given situation (`PREPARE_RECOVERY_PLAN` when SLA is breached, `ASSIGN_OWNER` when owner is missing, etc.) are still hand-coded business logic. Adding a brand-new action type doesn't automatically make any skill propose it; that would require either updating the skills or moving to a rules-engine / LLM-driven recommendation layer. The decision was that hand-coded skills are *correct* — they're deterministic, testable, and avoid the LLM hallucinating "which action type fits" — and the value of full data-driven recommendation is far below the cost of giving up that determinism.

**Production**:
- Replace the MCP TTL poll with a small LISTEN consumer thread for true real-time consistency between app and MCP.
- Promote the DB Explorer's edit-cell feature (planned) to support editing `action_catalogue.label/description/allowed_roles/is_active` directly — every change would then propagate through D-018 → D-019 with no admin SQL session needed.
- Consider a `catalogue_audit` table that captures every change to `action_catalogue` rows (who, when, what, why). Today the change is silent — the operator just edits a row.


### D-019 addendum · DELETE coverage in the realtime trigger

**Issue discovered**: D-018's trigger function intentionally covered only `INSERT/UPDATE` because the append-only invariant (D-017) said rows would never leave business/audit tables. That left `action_catalogue` — a *configuration* table where genuine DELETE is legitimate — without a propagation path. Deleting a row from the DB Explorer / psql would update Postgres, but the in-memory snapshot in the running app stayed stale and the UI didn't reflect the removal until manual page refresh.

**Fix** (migration `d019_delete_notifications.sql` + `init.sql` sync):
- The `notify_db_explorer()` trigger function now handles `DELETE` by reading `OLD` instead of `NEW` (via a `CASE TG_OP` branch). One function body handles all three operations.
- Every trigger is re-attached as `AFTER INSERT OR UPDATE OR DELETE`. The trigger merely reports — D-017's enforcement still lives at the application layer (repositories never emit DELETE for business/audit tables). If an operator or admin SQL session does delete a row anyway, the explorer now reflects it honestly within ~2 ms.
- Frontend (`templates/db_explorer.html`): the WS event handler now has a `DELETE` branch that finds the matching row in every rendered table (by PK against the cell `title`), plays a red fade animation for 900 ms, decrements the parent group's count badge, then removes the `<tr>`. No `/db-explorer/row/{id}` fetch is issued — there's nothing to hydrate from.
- The app's `action_catalogue.handle_event` already matched on `event.get('table') == 'action_catalogue'` regardless of op, so the snapshot reload path works unchanged.

**Verified**: insert→delete cycle now produces `8 → 9 → 8 active types` reloads with both `INSERT` and `DELETE` events propagated to WS clients within 2 ms. 121 pytest pass.


## D-020 · Action recommendations are data-driven via `action_recommendation_rules`

**Choice**: Replaced the three hardcoded recommendation paths (`recommend_next_action` MCP tool, `customer_escalation_summary` skill, `closure_readiness_check` skill) with a single rules engine that evaluates rows from a new `action_recommendation_rules` table. The engine matches in-memory *facts* (the inputs each recommender already computes) against JSONB *conditions* and returns the first match by `priority_order`. Operators can now add, retire, or reorder recommendations by editing the table — no developer commit, no service restart. The engine snapshot reloads via the realtime broadcaster (D-018) within ~2 ms of any DB change.

**Why** (the architectural exit from the D-019 "remaining gap"): D-019 made `action_catalogue` the live source of truth for which action types *exist*, but the three hand-coded recommenders meant a new action_type would never be *recommended* by the agent unless a developer wrote a new if/else branch. That created an awkward UX gap — operators could add to the menu but the agent ignored their additions. D-020 closes the gap by moving the picking logic itself into the DB.

**Why this design (JSONB conditions, first-match by priority, not a full rules engine)**:
- The existing hardcoded branches are simple — each is "X equals Y" or "X in [Y,Z]" or "X is null" combined with AND. A JSONB shape with a handful of operators covers every case cleanly.
- First-match-wins matches the original `if/elif/elif/else` semantics, so the migration is mechanical (rules at priority 10, 20, 30 … with the catch-all at 999).
- No expression parser. No sandboxed code execution. The whole evaluator is ~30 lines of Python. Unknown operators *fail closed* (the rule is skipped) so a typo in conditions can never accidentally match everything.

**Implementation**:
- **Table** (`infra/postgres/migrations/d020_recommendation_rules.sql` + `init.sql` sync): `rule_ref`, `recommender`, `priority_order`, `conditions JSONB`, `action_type FK→action_catalogue`, `recommended_priority`, `rationale_template`, `is_active`, `notes`. Index on `(recommender, priority_order) WHERE is_active`. CHECK constraint on `recommended_priority`. The D-018 realtime trigger is attached.
- **Seed** (13 rules): all 5 branches of the MCP tool + all 6 of `customer_escalation_summary` + all 2 of `closure_readiness_check`, with `priority_order` preserving the original if/elif evaluation order. Each rule has a `notes` field explaining its purpose for the eventual ops-facing editor UI.
- **App engine** (`src/acme_app/policy/recommendation_engine.py`): live snapshot pattern identical to D-019 — module-level `_rules` list, `refresh_from_db()` at startup, `handle_event()` registered as a realtime post-fan-out hook. Public API: `evaluate(recommender, facts) → Recommendation | None`. Fails to `None` rather than raising, so a missing rule set degrades to the caller's fallback rather than a 500.
- **MCP engine** (`mcp_server/src/acme_mcp/recommendation_engine.py`): sync psycopg loader with 5 s TTL cache, same pattern as D-019's `validation.py`. Identical operator vocabulary.
- **Wiring**: `customer_escalation_summary._recommend` and `closure_readiness_check` now call the engine and fall back to a safe default (`SCHEDULE_REVIEW`/`Low` and the original closure branches respectively) if the snapshot is empty. The MCP `recommend_next_action` tool does the same. **`closure_readiness_check` still composes its own rationale** because that text depends on the dynamic `missing` list — engine templates handle constant-rationales (`'All closure conditions satisfied.'`) cleanly but can't expand a runtime list.
- **DB Explorer**: `action_recommendation_rules` registered in `TABLE_COLUMNS`, `LINKS`, and `DEFAULT_ORDER`. `action_catalogue.action_type` now expands into "recommendation rules that propose this action" alongside "actions of this type". Operators can browse "which rules use this action_type" with one click.
- **Tests**: 12 new unit tests covering operator semantics (in, not_in, null, not_null, equality), first-match ordering, recommender isolation, template rendering with missing facts, and a regression that the seeded `customer_escalation_summary` rules reproduce the original 5 hardcoded branches on representative inputs. Full suite: 133 passed, 4 skipped.
- **Live verified**: inserted `REQUEST_LEGAL_REVIEW` into `action_catalogue` + a new rule routing `risk=High AND severity in (P2, P3)` to it, at `priority_order=25` (between the existing rules at 20 and 30). Within 2 ms the engine reloaded (13 → 14 rules) and `evaluate(...)` returned `REQUEST_LEGAL_REVIEW` for those inputs — without any code change.

**What stayed in code (deliberately)**:
- Each skill still *computes its facts* before calling the engine — risk classification (`domain.risk_rules.classify_simple`), "is there a resolution note?" text-searching, "is the recovery plan complete?" status check. These are domain calculations, not rules.
- Each skill still *composes its narrative summary* (the human-readable text the user sees). Templating narratives from the DB would need a real templating runtime (Jinja against operator-supplied templates) and is a much larger change.
- The MCP tool's static fallback (`CUSTOMER_FOLLOW_UP/Medium` when the engine snapshot is empty) lives in code. This is the safety net for a transient DB outage — the agent recommends *something* deterministic rather than refusing to answer.

**Operator-facing contract**:
- **To add a new recommendation**: INSERT into `action_catalogue` (if the action_type is new), then INSERT into `action_recommendation_rules`. The agent recommends it on the next matching turn — no restart, no deploy.
- **To retire one**: `UPDATE action_recommendation_rules SET is_active=false`. The engine excludes it on the next reload (sub-second app, ≤5 s MCP).
- **To reorder priority**: `UPDATE … SET priority_order=…`. Live.
- **Available fact keys per recommender** (documented in the migration comments and in `notes` on each row):
  - `recommend_next_action_tool` — `tier`, `severity`, `sla`, `owner`
  - `customer_escalation_summary` — `risk`, `has_owner`, `severity`
  - `closure_readiness_check` — `ready_to_close`

**Production**:
- Add the cell-edit UI to the DB Explorer for `action_recommendation_rules` so operators don't need direct SQL access. The append-only invariant (D-017) doesn't apply here — rules are configuration, not audit — but a `rule_changes` audit log capturing who edited which rule when is the right addition for a real deployment.
- Promote `closure_readiness_check`'s dynamic rationale into an engine feature (e.g. template operators like `{missing | join: '; '}`). Not blocking — the existing carve-out is small and self-documenting.
- Replace MCP's 5 s TTL with a LISTEN consumer thread for sub-second consistency across processes. Same recommendation as D-019.
- Consider a *rules tester* page: pick a recommender, type some facts, see which rule would match. Hugely useful for operators editing rules without re-deploying chat traffic to test them.

**Remaining skill data-drivening (out of scope, explicitly)**: a "full" data-driven skill layer would also move the *fact computation* (risk classification, missing-info detection) and *narrative templating* into tables. That's three more components — a domain-rules engine, a templating runtime, possibly a small DSL for declaring skill structure — and is genuinely a separate project. D-020 covers the recommendation half because that's where the value-per-line-of-code is highest: it's the half that ops asked for, and the half that gates "can a new action_type actually be used".


## D-021 · Admin-editable DB Explorer (append + in-place edit, schema-driven)

**Choice**: The DB Explorer is no longer read-only for a curated set of tables. Admins can append new records and edit existing cells directly in the UI for: `action_catalogue`, `action_recommendation_rules`, `customers`, `issues`, `issue_updates`, `users`, `user_roles`. These are tinted terracotta in the sidebar with a pencil mark. All other tables — the audit ledger (`agent_traces`, `trace_events`, `tool_call_logs`, `rbac_decisions`) and the eval tables — stay strictly read-only.

**Why these tables**: They are the configuration + business-data + identity surfaces where manual amendment is meaningful and safe. Editing them is how an operator demonstrates the system's flexibility (add a customer → the agent answers about it; add an action type + a recommendation rule → the agent proposes it; deactivate a user → they lose access). The audit tables are excluded because editing them would forge the immutable decision record (D-017).

**Editing model (per the demo requirements)**:
- **Root-only**: only the table currently selected as root is editable. Child tables in a drill-down are read-only — to edit one you select it as root. Keeps the mental model simple and the write target unambiguous.
- **Append**: a terracotta "+ Add record" button by the header opens an inline form. System fields (`id`, timestamps, `keycloak_subject`, business refs like `issue_ref`) are shown pre-filled/locked and synthesised server-side. Everything else is a control matched to the column type — booleans → toggle, fixed vocabularies (industry, tier, region, severity, status, side-effect level, role) → dropdown, FK columns → dropdown of valid target rows (resolved live), `TEXT[]` → multi-select chips, JSON → textarea. Minimum typing.
- **AI assist**: every free-text field (`name`, `title`, `description`, `label`, `rationale_template`, `notes`, `conditions`, …) has a "✨ AI" button that calls the local Ollama model with the other field values as context and fills a realistic sample value. Starts on the local model (cost-free); can be switched to a paid provider later by changing one call site. Falls back to a templated stub if the local model is offline so the button always does something.
- **Confirm/cancel** on the new-row form; **click-any-cell** in-place editing on existing rows with the same type-aware controls and ✓/✕ mini-buttons.
- **Live reflection**: appends and edits flow through the existing realtime triggers (D-018), so a committed change flashes into the grid via the WebSocket — the same path external changes use. The form doesn't optimistically insert; it waits for the authoritative WS event.

**Safety / implementation**:
- **Schema-driven, not free-form**: a per-column `EDIT_SPEC` in `application/db_explorer.py` declares each column's kind, options, FK target, AI-eligibility, and auto-generation strategy. The UI and the API both read it. A column not in the spec is treated as read-only `system`.
- **Server-side validation** in `routes_db_explorer.py`: `_coerce_value` enforces kind (bool/int/enum-membership/`TEXT[]`-membership/JSON-validity), rejects writes to system columns, and requires required fields. Table and column identifiers are allow-listed before any SQL; values are bound parameters or controlled raw expressions (`now()`, `gen_random_uuid()` default, generated refs). No identifier or value is interpolated raw from the request.
- **Append-only invariant preserved (D-017)**: endpoints only `INSERT` and `UPDATE`. No DELETE path was added. Deactivation is done by editing `is_active`/`status`/`deleted_at`, not by removing rows.
- **Admin-only**: all four new endpoints (`GET /db-explorer/edit-meta/{table}`, `POST /db-explorer/row/{table}`, `PATCH /db-explorer/row/{table}/{row_id}`, `POST /db-explorer/ai-suggest`) require the `admin` role and reject any non-editable table with 403.
- Every append/patch is logged with the acting username.

**Demo flows this unlocks**:
- *Live data*: append "Globex Corporation" to `customers`, then ask the chat "What's going on with Globex?" — the agent answers from data typed seconds earlier.
- *No-code behaviour change*: append an action type to `action_catalogue` + a rule to `action_recommendation_rules`, then watch the agent recommend a brand-new action (D-019 + D-020 propagate it live).
- *Identity*: add a user + grant a role, or flip `is_active` to deactivate — reflects in the auth path on next login (D-016).

**Production hardening (deferred)**:
- Per-field-level RBAC (today it's all-or-nothing admin).
- An edit audit table capturing who changed which cell from/to what (currently only the app log records it).
- Optimistic-concurrency tokens to detect concurrent edits.
- Swap the AI-assist model to a paid provider per field if local quality is insufficient (one call site).
- For genuinely new *tables* (not rows), the registry is still code — making the table/relationship registry itself data-driven is a larger, separate step.
