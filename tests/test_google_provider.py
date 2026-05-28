import pytest


def test_google_provider_parser_accepts_fenced_json():
    from acme_app.infrastructure.llm.providers.google_provider import _parse_json_object

    parsed = _parse_json_object('```json\n{"intent":"ok","steps":[]}\n```')

    assert parsed == {'intent': 'ok', 'steps': []}


@pytest.fixture
def fake_google(monkeypatch):
    from acme_app.config import settings

    monkeypatch.setattr(settings, 'google_api_key', 'test-key')
    captured = []

    class _Response:
        text = '{"intent":"ok","steps":[]}'

        class usage_metadata:
            prompt_token_count = 1
            candidates_token_count = 1

    class _Model:
        def __init__(self, model):
            self.model = model

        async def generate_content_async(self, _prompt, generation_config):
            captured.append(generation_config)
            return _Response()

    class _GenAI:
        @staticmethod
        def configure(api_key):
            assert api_key == 'test-key'

        GenerativeModel = _Model

    import sys
    monkeypatch.setitem(sys.modules, 'google.generativeai', _GenAI)

    return captured


@pytest.mark.asyncio
async def test_google_planner_uses_large_output_cap(fake_google):
    from acme_app.infrastructure.llm.providers.google_provider import GoogleProvider

    provider = GoogleProvider(model='gemini-test')
    response = await provider.plan('system', 'user', {})

    assert fake_google[-1]['max_output_tokens'] == 4096
    assert fake_google[-1]['response_mime_type'] == 'application/json'
    assert response.raw == {'intent': 'ok', 'steps': []}


@pytest.mark.asyncio
async def test_google_narration_uses_roomy_output_cap(fake_google):
    from acme_app.infrastructure.llm.providers.google_provider import GoogleProvider

    provider = GoogleProvider(model='gemini-test')
    await provider.narrate('system', 'user', {'fact': 'value'})

    assert fake_google[-1]['max_output_tokens'] == 2048
