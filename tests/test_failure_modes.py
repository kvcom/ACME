import pytest

from acme_app.infrastructure.llm.provider import get_provider


@pytest.mark.asyncio
async def test_ollama_falls_back_when_unreachable():
    """Ollama provider is now real (HTTP). When Ollama is unreachable, it must
    fall back to the stub planner rather than raising — the demo stays alive
    even without a local LLM."""
    import json
    provider = get_provider('ollama-llama')
    # The base_url defaults to host.docker.internal; from the test runner this
    # is not resolvable, so the call hits the HTTP error path and falls back.
    resp = await provider.plan('sys', 'brief me on Northwind', {'role': 'sales_user'})
    # Whatever happens, we must get back a valid plan JSON (real or stub).
    parsed = json.loads(resp.text)
    assert 'intent' in parsed
    assert 'steps' in parsed


@pytest.mark.asyncio
async def test_stub_provider_always_returns_json():
    import json
    provider = get_provider('stub')
    resp = await provider.plan('sys', 'brief me on Northwind', {'role': 'sales_user'})
    parsed = json.loads(resp.text)
    assert 'intent' in parsed
    assert 'steps' in parsed


def test_unknown_provider_falls_back_to_stub():
    provider = get_provider('nonexistent')
    assert provider.name == 'stub'


def test_model_registry_has_all_four_providers():
    from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY
    providers_present = {spec.provider for spec in MODEL_REGISTRY.values()}
    assert {'stub', 'anthropic', 'openai', 'google', 'ollama'} <= providers_present
