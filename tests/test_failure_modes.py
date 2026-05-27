import pytest

from acme_app.infrastructure.llm.provider import get_provider


@pytest.mark.asyncio
async def test_ollama_stub_raises_runtime_error():
    provider = get_provider('ollama')
    with pytest.raises(RuntimeError, match='LLM unavailable'):
        await provider.plan('sys', 'user', {})


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
