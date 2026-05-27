from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=('.env', '.env.example'),
        env_file_encoding='utf-8',
        extra='ignore',
    )

    app_env: str = 'dev'
    llm_provider: str = 'stub'
    llm_model: str = 'stub-planner-v1'

    anthropic_api_key: str = ''
    anthropic_model: str = 'claude-sonnet-4-20250514'
    openai_api_key: str = ''
    openai_model: str = 'gpt-4o-mini'

    database_url: str = 'postgresql+asyncpg://acme:acme@postgres:5432/acme'
    sync_database_url: str = 'postgresql://acme:acme@postgres:5432/acme'
    redis_url: str = 'redis://redis:6379/0'

    keycloak_url: str = 'http://keycloak:8080'
    keycloak_realm: str = 'acme'
    keycloak_client_id: str = 'acme-assistant'
    keycloak_client_secret: str = ''

    mcp_server_url: str = 'http://mcp-server:8001'
    otel_exporter_otlp_endpoint: str = 'http://otel-collector:4318'
    otel_service_name: str = 'acme-app'

    confirmation_hmac_secret: str = 'dev-only-secret-change-me'
    debug_endpoints_enabled: bool = True


settings = Settings()
