from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=('.env.example', '.env'),
        env_file_encoding='utf-8',
        env_ignore_empty=True,
        extra='ignore',
    )

    app_env: str = 'dev'
    llm_provider: str = 'claude-opus-4-8'
    llm_model: str = 'claude-opus-4-8'

    anthropic_api_key: str = ''
    anthropic_model: str = 'claude-sonnet-4-6'
    openai_api_key: str = ''
    openai_model: str = 'gpt-5.4-mini'
    google_api_key: str = ''
    google_model: str = 'gemini-3.5-flash'
    ollama_base_url: str = 'http://host.docker.internal:11434'

    database_url: str = 'postgresql+asyncpg://acme:acme@postgres:5432/acme'
    sync_database_url: str = 'postgresql://acme:acme@postgres:5432/acme'
    redis_url: str = 'redis://redis:6379/0'

    keycloak_url: str = 'http://keycloak:8080'
    keycloak_realm: str = 'acme'
    keycloak_client_id: str = 'acme-assistant'
    keycloak_client_secret: str = ''
    keycloak_admin_username: str = 'admin'
    keycloak_admin_password: str = 'admin'
    keycloak_session_enforcement_enabled: bool = True

    mcp_server_url: str = 'http://mcp-server:8001'
    otel_exporter_otlp_endpoint: str = 'http://otel-collector:4318'
    otel_service_name: str = 'acme-app'
    otel_jaeger_ui_url: str = 'http://localhost:16686'
    otel_jaeger_query_url: str = 'http://jaeger:16686'

    confirmation_hmac_secret: str = 'dev-only-secret-change-me'
    # Separate secret for signing the session cookie. Defaults to the dev
    # placeholder; MUST be overridden outside dev (see _guard_secrets()).
    session_signing_secret: str = 'dev-only-session-secret-change-me'
    # Verify Keycloak JWT signatures against the realm JWKS for bearer-token
    # auth. Default on. Set false only for an offline/no-Keycloak demo.
    jwt_verify_signature: bool = True
    debug_endpoints_enabled: bool = True
    demo_auth_fallback_enabled: bool = True
    demo_session_max_age_seconds: int = 8 * 3600
    session_heartbeat_seconds: int = 5


_DEFAULT_SECRETS = {
    'confirmation_hmac_secret': 'dev-only-secret-change-me',
    'session_signing_secret': 'dev-only-session-secret-change-me',
}


def _guard_secrets(s: 'Settings') -> None:
    """Fail loudly if the default placeholder secrets are still in use outside
    a dev environment. In dev we only warn so the demo runs out of the box."""
    import logging
    offenders = [k for k, v in _DEFAULT_SECRETS.items() if getattr(s, k) == v]
    if not offenders:
        return
    msg = (
        'Default placeholder secret(s) still in use: '
        + ', '.join(offenders)
        + '. Override these env vars before any non-dev deployment.'
    )
    if s.app_env.lower() not in ('dev', 'development', 'local', 'test'):
        raise RuntimeError(msg)
    logging.getLogger(__name__).warning('SECURITY: %s', msg)


settings = Settings()
_guard_secrets(settings)
