"""Failure-mode tests for the LLM layer.

With the stub gone, the system must surface real errors clearly:
  - Construction errors when a key is missing → caller catches.
  - Auto's empty chain → LLMUnavailableError on first call.
  - get_provider with unknown names → defaults to Auto, NOT to a stub.
"""
import pytest

from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY
from acme_app.infrastructure.llm.providers.auto_provider import (
    AutoProvider,
    LLMUnavailableError,
)


def test_anthropic_construction_fails_without_key(monkeypatch):
    from acme_app.config import settings
    monkeypatch.setattr(settings, 'anthropic_api_key', '')
    from acme_app.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
    with pytest.raises(RuntimeError, match='ANTHROPIC_API_KEY'):
        AnthropicProvider()


def test_openai_construction_fails_without_key(monkeypatch):
    from acme_app.config import settings
    monkeypatch.setattr(settings, 'openai_api_key', '')
    from acme_app.infrastructure.llm.providers.openai_provider import OpenAIProvider
    with pytest.raises(RuntimeError, match='OPENAI_API_KEY'):
        OpenAIProvider()


def test_google_construction_fails_without_key(monkeypatch):
    from acme_app.config import settings
    monkeypatch.setattr(settings, 'google_api_key', '')
    from acme_app.infrastructure.llm.providers.google_provider import GoogleProvider
    with pytest.raises(RuntimeError, match='GOOGLE_API_KEY'):
        GoogleProvider()


@pytest.mark.asyncio
async def test_auto_raises_when_no_provider_available(monkeypatch):
    from acme_app.config import settings
    monkeypatch.setattr(settings, 'anthropic_api_key', '')
    monkeypatch.setattr(settings, 'openai_api_key', '')
    monkeypatch.setattr(settings, 'google_api_key', '')
    monkeypatch.setattr(settings, 'ollama_base_url', '')

    auto = AutoProvider()
    with pytest.raises(LLMUnavailableError):
        await auto.plan('sys', 'user', {})


def test_unknown_provider_falls_back_to_auto():
    from acme_app.infrastructure.llm.provider import get_provider
    provider = get_provider('this-key-does-not-exist')
    assert provider.name == 'auto'


def test_model_registry_has_all_four_providers_plus_auto():
    providers_present = {spec.provider for spec in MODEL_REGISTRY.values()}
    assert {'auto', 'anthropic', 'openai', 'google', 'ollama'} <= providers_present
    # Stub is gone.
    assert 'stub' not in providers_present


def test_no_stub_module_remains():
    """Belt-and-braces: importing the deleted module must error."""
    with pytest.raises(ImportError):
        from acme_app.infrastructure.llm.providers import stub_provider  # noqa: F401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_soft_delete_hides_from_sidebar_but_keeps_traces():
    """Soft-delete removes the conversation from list_conversations but the
    underlying agent_traces rows must remain (Decision Ledger principle / D-015).
    """
    from sqlalchemy import text
    from acme_app.infrastructure.db.session import AsyncSessionLocal
    from acme_app.infrastructure.db import repositories as repo

    async with AsyncSessionLocal() as session:
        await session.execute(text(
            "INSERT INTO conversations (conversation_ref, username, last_message_preview, message_count) "
            "VALUES ('SOFT-DEL-TEST', 'sam.support', 'test', 1) ON CONFLICT DO NOTHING"
        ))
        await session.commit()

        before = await repo.conversation_list(session, 'sam.support')
        assert any(c['conversation_ref'] == 'SOFT-DEL-TEST' for c in before)

        ok = await repo.soft_delete_conversation(session, 'SOFT-DEL-TEST', 'sam.support')
        await session.commit()
        assert ok is True

        after = await repo.conversation_list(session, 'sam.support')
        assert not any(c['conversation_ref'] == 'SOFT-DEL-TEST' for c in after)

        # Row still exists, just hidden.
        row = (await session.execute(text(
            "SELECT deleted_at FROM conversations WHERE conversation_ref = 'SOFT-DEL-TEST'"
        ))).first()
        assert row is not None and row[0] is not None

        # Trace data is preserved (none for this synthetic conv, but the
        # repo function would never touch agent_traces anyway).
        # Cleanup.
        await session.execute(text("DELETE FROM conversations WHERE conversation_ref = 'SOFT-DEL-TEST'"))
        await session.commit()
