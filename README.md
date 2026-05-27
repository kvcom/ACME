# Acme Operations Assistant

Secure, auditable local prototype of an agentic enterprise assistant for customer operations.

## Design Lineage
This prototype synthesizes patterns from Nabu One (decision ledger, modular monolith), myTbot (AI advises, rules execute), Up & Loud (evidence-first responses), and Barescope (clear verification badges).

## Quick start
1. Copy `.env.example` to `.env` and fill API keys.
2. Run `docker compose up --build`.
3. Open `http://localhost:8000`.

## Demo users
- `sarah.sales` / `password` (sales_user)
- `sam.support` / `password` (support_user)
- `admin.acme` / `password` (admin)

## Key workflows
- Streaming chat with dynamic tool selection.
- Propose-confirm flow for writes.
- Role-based access control through Keycloak roles.
- Trace viewer with Evidence-to-Action graph.

## PostgreSQL vs Redis
- PostgreSQL stores durable business truth.
- Redis stores short-term conversation context and pending action proposals.
