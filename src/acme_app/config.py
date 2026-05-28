from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=('.env.example', '.env'),
        env_file_encoding='utf-8',
        env_ignore_empty=True,
        extra='ignore',
    )

    app_env: str = 'dev'
    llm_provider: str = 'gpt-5.4-mini'
    llm_model: str = 'gpt-5.4-mini'

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

    mcp_server_url: str = 'http://mcp-server:8001'
    otel_exporter_otlp_endpoint: str = 'http://otel-collector:4318'
    otel_service_name: str = 'acme-app'

    confirmation_hmac_secret: str = 'dev-only-secret-change-me'
    debug_endpoints_enabled: bool = True


settings = Settings()
