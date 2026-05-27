# Architecture

## Context
- FastAPI app hosts UI and API.
- Keycloak handles auth and role claims.
- Custom MCP server exposes business tools backed by PostgreSQL.
- Redis stores working memory and pending actions.
- OpenTelemetry exports spans; decision traces are persisted and rendered in app.

## Core sequence
1. User query enters `/chat/stream`.
2. Adversarial check and PII redaction run.
3. Planner creates tool/skill plan.
4. MCP tools execute.
5. Optional action is proposed and requires confirmation token.
6. Final answer + trace metadata stream to UI.
