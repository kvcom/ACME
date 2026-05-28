"""Tests for the planner + AutoProvider routing.

The stub planner is gone — these tests now verify the Auto routing chain
(availability detection, ordering, fallback behaviour) and the planner's
recovery from malformed LLM JSON.
"""

import pytest

from acme_app.application.planner import create_plan
from acme_app.infrastructure.llm.providers.auto_provider import (
    PRIORITY_CHAIN,
    AutoProvider,
    LLMUnavailableError,
)


def test_priority_chain_orders_local_first():
    """Local Ollama models must come before any cloud model in the chain."""
    chain = PRIORITY_CHAIN
    first_cloud = next(
        i for i, k in enumerate(chain) if not k.startswith('ollama-')
    )
    assert all(chain[i].startswith('ollama-') for i in range(first_cloud))


def test_priority_chain_cheapest_cloud_first():
    """Among cloud models, the chain starts with the fast fallback choices."""
    cloud = [k for k in PRIORITY_CHAIN if not k.startswith('ollama-')]
    assert cloud[:2] == ['gpt-5.4-mini', 'gemini-3.5-flash']


def test_auto_provider_no_keys_results_in_unavailable(monkeypatch):
    """With no API keys configured, Auto's chain is empty and plan() raises."""
    from acme_app.config import settings
    monkeypatch.setattr(settings, 'anthropic_api_key', '')
    monkeypatch.setattr(settings, 'openai_api_key', '')
    monkeypatch.setattr(settings, 'google_api_key', '')
    monkeypatch.setattr(settings, 'ollama_base_url', '')

    auto = AutoProvider()
    assert auto.available_chain() == []


def test_auto_provider_with_one_key_picks_that_one(monkeypatch):
    from acme_app.config import settings
    monkeypatch.setattr(settings, 'anthropic_api_key', 'sk-test')
    monkeypatch.setattr(settings, 'openai_api_key', '')
    monkeypatch.setattr(settings, 'google_api_key', '')
    monkeypatch.setattr(settings, 'ollama_base_url', '')

    auto = AutoProvider()
    chain = auto.available_chain()
    assert chain
    assert all(k.startswith('claude') for k in chain)


def test_auto_provider_prefers_local_when_present(monkeypatch):
    from acme_app.config import settings
    monkeypatch.setattr(settings, 'anthropic_api_key', 'sk-test')
    monkeypatch.setattr(settings, 'openai_api_key', '')
    monkeypatch.setattr(settings, 'google_api_key', '')
    monkeypatch.setattr(settings, 'ollama_base_url', 'http://example:11434')

    auto = AutoProvider()
    chain = auto.available_chain()
    assert chain[0].startswith('ollama-')


@pytest.mark.asyncio
async def test_auto_provider_falls_through_malformed_plan(monkeypatch):
    """Auto should skip a model that returns syntactically weak plan JSON."""
    from acme_app.config import settings
    from acme_app.infrastructure.llm.providers import auto_provider as auto_mod
    from acme_app.infrastructure.llm.providers.base import LLMResponse

    monkeypatch.setattr(settings, 'anthropic_api_key', '')
    monkeypatch.setattr(settings, 'openai_api_key', 'sk-test')
    monkeypatch.setattr(settings, 'google_api_key', '')
    monkeypatch.setattr(settings, 'ollama_base_url', 'http://example:11434')

    class _MalformedProvider:
        async def plan(self, *_a, **_kw):
            return LLMResponse(
                text='{"intent": null, "steps": ["broken"], "write_requested": false}',
                prompt_tokens=1, completion_tokens=1, latency_ms=1,
                model='bad-local', raw={'intent': None, 'steps': ['broken']},
            )

    class _GoodProvider:
        async def plan(self, *_a, **_kw):
            return LLMResponse(
                text='{"intent": "ok", "steps": [], "write_requested": false}',
                prompt_tokens=1, completion_tokens=1, latency_ms=1,
                model='good-cloud',
                raw={'intent': 'ok', 'steps': [], 'write_requested': False},
            )

    def _construct(spec):
        if spec.provider == 'ollama':
            return _MalformedProvider()
        return _GoodProvider()

    monkeypatch.setattr(auto_mod, '_construct', _construct)

    response = await AutoProvider().plan('', '', {})
    assert response.model == 'good-cloud'


@pytest.mark.asyncio
async def test_create_plan_recovers_from_malformed_json(monkeypatch):
    """If the LLM returns gibberish, the planner emits a clarification plan."""
    from acme_app.infrastructure.llm.providers.base import LLMResponse

    class _BadProvider:
        async def plan(self, *_a, **_kw):
            return LLMResponse(text='not json at all',
                               prompt_tokens=10, completion_tokens=2,
                               latency_ms=1, model='bad')

    # planner.py binds the name at import time, so patch on the planner module
    import acme_app.application.planner as planner_mod
    monkeypatch.setattr(planner_mod, 'get_provider', lambda *_a, **_kw: _BadProvider())

    plan, _resp = await create_plan('hello?', 'auto', {'role': 'sales_user'})
    assert plan.requires_clarification is True
    assert plan.steps == []


@pytest.mark.asyncio
async def test_create_plan_uses_provider_raw_payload(monkeypatch):
    """Provider-level JSON cleanup should be honored by create_plan."""
    from acme_app.infrastructure.llm.providers.base import LLMResponse

    class _Provider:
        async def plan(self, *_a, **_kw):
            return LLMResponse(
                text='```json\n{"intent":"wrapped","steps":[]}\n```',
                prompt_tokens=10, completion_tokens=2,
                latency_ms=1, model='wrapped',
                raw={'intent': 'wrapped', 'steps': []},
            )

    import acme_app.application.planner as planner_mod
    monkeypatch.setattr(planner_mod, 'get_provider', lambda *_a, **_kw: _Provider())

    plan, _resp = await create_plan('hello?', 'auto', {'role': 'sales_user'})
    assert plan.intent == 'wrapped'
    assert plan.requires_clarification is False


def test_llm_unavailable_error_is_runtime_error():
    """The orchestrator catches it via the broad except clause."""
    err = LLMUnavailableError('no llm')
    assert isinstance(err, RuntimeError)
