# Failure Modes

| Dependency | Behaviour |
|---|---|
| LLM provider unavailable | Graceful error and no writes |
| Keycloak unavailable | Auth fails and user cannot proceed |
| PostgreSQL unavailable | API degrades for data-backed operations |
| Redis unavailable | Memory features degrade, core read paths still possible |
| MCP unavailable | Tool call error shown with trace reference |
| OTel unavailable | App continues, traces still available in app ledger |
