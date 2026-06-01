"""Tests for LLM availability resolution (used by the DB Explorer AI assist
and the composer's model dropdown)."""
import pytest

from acme_app.config import settings
from acme_app.infrastructure.llm import availability


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    """Start every test from a clean slate: no cloud keys, no local server."""
    monkeypatch.setattr(settings, 'anthropic_api_key', '')
    monkeypatch.setattr(settings, 'openai_api_key', '')
    monkeypatch.setattr(settings, 'google_api_key', '')
    monkeypatch.setattr(settings, 'ollama_base_url', '')


@pytest.mark.asyncio
async def test_nothing_configured_means_no_model():
    assert await availability.any_model_available() is False
    assert await availability.pick_assist_spec() is None
    avail = await availability.model_availability()
    assert avail and all(v is False for v in avail.values())


@pytest.mark.asyncio
async def test_anthropic_key_makes_anthropic_models_available(monkeypatch):
    monkeypatch.setattr(settings, 'anthropic_api_key', 'sk-test')
    avail = await availability.model_availability()
    assert avail['claude-opus-4-8'] is True
    assert avail['claude-sonnet-4-6'] is True
    assert avail['gpt-5.5'] is False
    # With only cloud models available, the assist picks the cheapest by output.
    spec = await availability.pick_assist_spec()
    assert spec is not None
    assert spec.key == 'claude-sonnet-4-6'  # 0.015 < opus 0.025


@pytest.mark.asyncio
async def test_reachable_local_is_preferred_for_assist(monkeypatch):
    monkeypatch.setattr(settings, 'anthropic_api_key', 'sk-test')

    async def _reachable():
        return True

    monkeypatch.setattr(availability, 'ollama_reachable', _reachable)
    spec = await availability.pick_assist_spec()
    assert spec is not None
    assert spec.provider == 'ollama'  # free local wins over paid cloud


@pytest.mark.asyncio
async def test_unreachable_local_is_not_offered(monkeypatch):
    # base_url set but server not actually up → must be unavailable.
    monkeypatch.setattr(settings, 'ollama_base_url', 'http://host.docker.internal:11434')

    async def _unreachable():
        return False

    monkeypatch.setattr(availability, 'ollama_reachable', _unreachable)
    avail = await availability.model_availability()
    assert avail['ollama-llama'] is False
    assert await availability.any_model_available() is False
