# Vulnerability and Security Testing

This folder contains the Trivy evidence for the ACME project. It records the current filesystem scan, the Docker image scans for the active Compose stack, and the summary showing that the current security scan surface is clean: 0 critical, 0 high, 0 medium, 0 low, 0 misconfigurations, and 0 detected secrets.

## Tooling

The project uses Trivy as the open source vulnerability and security scanner. It scans:

- OS packages inside Docker images.
- Language dependencies inside images.
- Project filesystem vulnerabilities.
- Infrastructure-as-code misconfigurations.
- Secrets in the repository.

The latest reports were generated with the official Trivy Docker image because the host `trivy` binary was not on `PATH`:

```powershell
docker run --rm -v "${PWD}:/work" -w /work aquasec/trivy:0.70.0 fs `
  --skip-version-check `
  --format json `
  --scanners vuln,secret,misconfig `
  --skip-dirs security/trivy `
  --skip-dirs _design_pkg `
  --output security/trivy/trivy-fs-report.json .
```

Image scans use the Docker socket so Trivy can inspect local images built by Compose:

```powershell
docker run --rm `
  -v /var/run/docker.sock:/var/run/docker.sock `
  -v "${PWD}:/work" `
  -w /work `
  aquasec/trivy:0.70.0 image `
  --skip-version-check `
  --format json `
  --output security/trivy/trivy-image-acme-app.json `
  acme-app:latest
```

The same image command was run for:

- `acme-app:latest`
- `acme-mcp-server:latest`
- `acme-keycloak:latest`
- `acme-postgres:latest`
- `jaegertracing/jaeger:2.18.0`
- `otel/opentelemetry-collector-contrib:0.153.0`
- `valkey/valkey:8.1-alpine`

## Current Result

See [SUMMARY.md](SUMMARY.md) for the generated table. The current scan set is all zeroes across:

- Critical vulnerabilities.
- High vulnerabilities.
- Medium vulnerabilities.
- Low vulnerabilities.
- Unknown vulnerabilities.
- Misconfigurations.
- Secrets.

## What Changed To Reach Zero

### Application Image

The app image was moved from a Debian slim Python base to `python:3.13-alpine`. The Dockerfile now runs:

- `apk upgrade --no-cache` to pull fixed Alpine packages such as `xz-libs`.
- `python -m pip install --upgrade "pip>=26.1"` to remove Trivy's flagged `pip` findings.
- A non-root `app` user.
- A Dockerfile healthcheck.

The app dependency set was also changed from `python-jose[cryptography]` to `PyJWT[crypto]`, removing the vulnerable `ecdsa` dependency while keeping JWT verification explicit and test-covered.

### MCP Server Image

The MCP server image received the same base-image hardening as the app:

- `python:3.13-alpine`.
- `apk upgrade --no-cache`.
- `pip>=26.1`.
- Non-root `app` user.
- Healthcheck.

This removed the remaining medium findings in the MCP server image.

### PostgreSQL Image

Postgres now uses a small local image built from `postgres:16.13-alpine` instead of the older direct image reference. The local image:

- Runs `apk upgrade --no-cache`.
- Installs Alpine `su-exec`.
- Replaces the vulnerable Go-based `gosu` path in the entrypoint with `su-exec`.
- Runs as the `postgres` user.
- Defines a Dockerfile healthcheck.

That removed the remaining package findings from the database image while preserving normal Postgres startup behaviour.

### Keycloak Image

Keycloak is the most sensitive image because it ships a large Java distribution. The project now builds a local `acme-keycloak` image using the official Keycloak 26.6.2 distribution, then applies narrow fixes:

- Runs on an upgraded Java 21 Alpine runtime.
- Keeps the official Keycloak distribution as the application source.
- Patches vulnerable Netty jars with fixed upstream jar contents while preserving the filenames expected by the distribution.
- Patches the flagged OpenTelemetry API jar with the fixed upstream jar.
- Removes the unused SQL Server JDBC jar after Keycloak augmentation.
- Runs Keycloak as a non-root user.
- Uses a Dockerfile healthcheck.

This was validated by rebuilding the image, starting Keycloak, confirming the service was healthy, and checking the realm metadata endpoint returned HTTP 200.

### Observability Images

The observability proof focuses on:

- OpenTelemetry Collector.
- Jaeger.
- The custom trace viewer in the app.

The remaining observability images scan clean:

- `jaegertracing/jaeger:2.18.0`
- `otel/opentelemetry-collector-contrib:0.153.0`

### Redis Replacement

The cache service now uses `valkey/valkey:8.1-alpine`, which scans clean in the current Trivy report set.

## Validation Performed

After the hardening changes, the following checks were run:

```powershell
docker compose build app mcp-server keycloak
docker compose up -d app mcp-server keycloak
docker compose config --quiet
pytest tests/test_auth.py tests/test_chat_response_metadata.py tests/test_failure_modes.py
```

Results:

- App, MCP server, Keycloak, Postgres, Redis/Valkey, OpenTelemetry Collector, and Jaeger were running.
- App, MCP server, Keycloak, and Postgres healthchecks were healthy.
- Keycloak realm metadata returned HTTP 200.
- Tests passed: 37 passed, 1 skipped.
- Trivy reports were regenerated under this folder.

## Important Caveat

This is a local technical-assessment prototype, not a production image-maintenance process. The clean Trivy result is useful evidence that the current local stack has no known findings under Trivy 0.70.0 and its vulnerability databases at scan time. In production, the preferred long-term path is to consume upstream fixed images as vendors release them, avoid custom jar patching where possible, and run Trivy continuously in CI so new CVEs are caught as the databases change.
