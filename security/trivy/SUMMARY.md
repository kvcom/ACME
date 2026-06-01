# Trivy Scan Summary

Generated: 2026-06-01 18:49 Europe/London

Scope:
- Filesystem scan of the project, excluding `security/trivy` and `_design_pkg`.
- Current Docker Compose images only.
- Retired/experimental reports were moved to `security/trivy/archive/`.

| Report | Critical | High | Medium | Low | Unknown | Fix available | Misconfig | Secrets |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `trivy-fs-report.json` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `trivy-image-acme-app.json` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `trivy-image-acme-keycloak.json` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `trivy-image-acme-mcp-server.json` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `trivy-image-acme-postgres.json` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `trivy-image-jaeger-2.18.0.json` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `trivy-image-otel-collector-contrib-0.153.0.json` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `trivy-image-valkey-8.1-alpine.json` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Notes:
- All current filesystem and image scans are clear of Trivy findings, including medium and low vulnerabilities, misconfigurations, and secrets.
- The app and MCP server were moved to `python:3.13-alpine`, run `apk upgrade --no-cache`, and upgrade `pip` during image build; the app also replaced `python-jose` with `PyJWT[crypto]` to remove the vulnerable `ecdsa` dependency.
- Postgres now uses a tiny local image from `postgres:16.13-alpine` with `apk upgrade --no-cache`, Alpine `su-exec` replacing the vulnerable Go-based `gosu`, a non-root runtime user, and a Dockerfile healthcheck.
- Keycloak now uses the official Keycloak 26.6.2 distribution on an upgraded Java 21 Alpine runtime, patched Netty and OpenTelemetry jars, `dev-file` augmentation, no unused SQL Server JDBC driver, and a Dockerfile healthcheck.
