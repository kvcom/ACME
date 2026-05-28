"""Tests for the planner + AutoProvider routing.

The stub planner is gone — these tests now verify the Auto routing chain
(availability detection, ordering, fallback behaviour) and the planner's
recovery from malformed LLM JSON.
"""

import pytest

from acme_app.application.planner import create_plan
from acme_app.infrastructure.llm.providers.auto_provider import (
    PRIORITY_CHAIN,
    ROUTE_CHAINS,
    AutoProvider,
    LLMUnavailableError,
    RouteDecision,
    _deterministic_route,
)


def test_priority_chain_has_single_local_model_first():
    """Auto exposes only Llama as the local model; Qwen is removed."""
    chain = PRIORITY_CHAIN
    assert chain[0] == 'ollama-llama'
    assert 'ollama-qwen' not in chain


def test_priority_chain_cheapest_cloud_first():
    """Among cloud models, the chain starts with the fast fallback choices."""
    cloud = [k for k in PRIORITY_CHAIN if not k.startswith('ollama-')]
    assert cloud[:2] == ['gpt-5.4-mini', 'gemini-3.5-flash']


def test_route_chains_skip_local_for_write_workflows():
    """Two-stage Auto should not use local execution for write planning."""
    assert ROUTE_CHAINS['write_proposal'][0] == 'gpt-5.4-mini'
    assert all(not key.startswith('ollama-') for key in ROUTE_CHAINS['write_proposal'])


def test_deterministic_auto_route_for_recommended_next_step():
    decision = _deterministic_route(
        'I have a call with Northwind today. What are the open issues, latest status, '
        'and recommended next step?'
    )

    assert decision.route == 'recommendation'
    assert decision.confidence == 1.0
    assert decision.source == 'rules'


def test_deterministic_auto_route_write_takes_precedence():
    decision = _deterministic_route(
        'For Northwind issue ISS-102, prepare a high-priority recovery plan action.'
    )

    assert decision.route == 'write_proposal'


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


def test_auto_provider_exposes_local_when_present(monkeypatch):
    from acme_app.config import settings
    monkeypatch.setattr(settings, 'anthropic_api_key', 'sk-test')
    monkeypatch.setattr(settings, 'openai_api_key', '')
    monkeypatch.setattr(settings, 'google_api_key', '')
    monkeypatch.setattr(settings, 'ollama_base_url', 'http://example:11434')

    auto = AutoProvider()
    chain = auto.available_chain()
    assert chain[0] == 'ollama-llama'
    assert 'ollama-qwen' not in chain


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
async def test_auto_provider_uses_agreed_route(monkeypatch):
    """When rules and Llama agree, Auto uses that route without arbitration."""
    from acme_app.config import settings
    from acme_app.infrastructure.llm.providers import auto_provider as auto_mod
    from acme_app.infrastructure.llm.providers.base import LLMResponse

    monkeypatch.setattr(settings, 'anthropic_api_key', 'sk-test')
    monkeypatch.setattr(settings, 'openai_api_key', 'sk-test')
    monkeypatch.setattr(settings, 'google_api_key', '')
    monkeypatch.setattr(settings, 'ollama_base_url', 'http://example:11434')

    class _Classifier:
        async def narrate(self, *_a, **_kw):
            return LLMResponse(
                text='{"route":"write_proposal","confidence":0.93,"reason":"action verb"}',
                prompt_tokens=1, completion_tokens=1, latency_ms=1,
                model='llama-classifier',
            )

    class _Executor:
        def __init__(self, model):
            self.model = model

        async def plan(self, *_a, **_kw):
            return LLMResponse(
                text='{"intent":"write","steps":[],"write_requested":true}',
                prompt_tokens=1, completion_tokens=1, latency_ms=1,
                model=self.model,
                raw={'intent': 'write', 'steps': [], 'write_requested': True},
            )

    def _construct(spec):
        if spec.provider == 'ollama':
            return _Classifier()
        return _Executor(spec.model)

    monkeypatch.setattr(auto_mod, '_construct', _construct)

    auto = AutoProvider()
    response = await auto.plan('', 'prepare action for ISS-123', {})
    assert auto.last_route is not None
    assert auto.last_route.route == 'write_proposal'
    assert auto.last_route.source == 'rules+llama'
    assert response.model == 'gpt-5.4-mini'


@pytest.mark.asyncio
async def test_auto_provider_uses_arbiter_on_classifier_disagreement(monkeypatch):
    from acme_app.config import settings
    from acme_app.infrastructure.llm.providers import auto_provider as auto_mod
    from acme_app.infrastructure.llm.providers.base import LLMResponse

    monkeypatch.setattr(settings, 'anthropic_api_key', '')
    monkeypatch.setattr(settings, 'openai_api_key', 'sk-test')
    monkeypatch.setattr(settings, 'google_api_key', '')
    monkeypatch.setattr(settings, 'ollama_base_url', 'http://example:11434')

    class _LocalClassifier:
        async def narrate(self, *_a, **_kw):
            return LLMResponse(
                text='{"route":"customer_read","confidence":0.7,"reason":"local read"}',
                prompt_tokens=1, completion_tokens=1, latency_ms=1,
                model='llama-classifier',
            )

    class _CloudProvider:
        def __init__(self, model):
            self.model = model

        async def narrate(self, *_a, **_kw):
            return LLMResponse(
                text='{"route":"recommendation","confidence":0.95,"reason":"arbiter sees next step"}',
                prompt_tokens=1, completion_tokens=1, latency_ms=1,
                model=self.model,
            )

        async def plan(self, *_a, **_kw):
            return LLMResponse(
                text='{"intent":"recommend","steps":[],"write_requested":false}',
                prompt_tokens=1, completion_tokens=1, latency_ms=1,
                model=self.model,
                raw={'intent': 'recommend', 'steps': [], 'write_requested': False},
            )

    def _construct(spec):
        if spec.provider == 'ollama':
            return _LocalClassifier()
        return _CloudProvider(spec.model)

    monkeypatch.setattr(auto_mod, '_construct', _construct)

    auto = AutoProvider()
    response = await auto.plan(
        '',
        'I have a call with Northwind today. What are the open issues, latest status, and recommended next step?',
        {},
    )

    assert auto.last_route == RouteDecision(
        route='recommendation',
        confidence=0.95,
        reason='arbiter sees next step',
        source='arbiter:gpt-5.4-mini',
    )
    assert response.model == 'gpt-5.4-mini'


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


@pytest.mark.asyncio
async def test_create_plan_infers_unknown_intent_from_valid_steps(monkeypatch):
    """Local models sometimes omit intent while still returning useful steps."""
    from acme_app.infrastructure.llm.providers.base import LLMResponse

    class _Provider:
        async def plan(self, *_a, **_kw):
            return LLMResponse(
                text='{"intent": null, "steps": []}',
                prompt_tokens=10, completion_tokens=2,
                latency_ms=1, model='local',
                raw={
                    'intent': None,
                    'requires_clarification': False,
                    'steps': [
                        {
                            'step_type': 'tool',
                            'name': 'get_open_issues',
                            'arguments': {'customer_name': 'Northwind'},
                            'rationale': 'read open issues',
                        },
                    ],
                },
            )

    import acme_app.application.planner as planner_mod
    monkeypatch.setattr(planner_mod, 'get_provider', lambda *_a, **_kw: _Provider())

    plan, _resp = await create_plan(
        'I have a call with Northwind today. What are the open issues?',
        'ollama-llama',
        {'role': 'support_user'},
    )

    assert plan.intent == 'customer_status'
    assert [step.name for step in plan.steps] == [
        'get_open_issues',
        'get_customer_profile',
        'customer_escalation_summary',
    ]


@pytest.mark.asyncio
async def test_create_plan_adds_customer_status_fallback_when_local_returns_no_steps(monkeypatch):
    """A clear customer briefing should not become a 0-step local response."""
    from acme_app.infrastructure.llm.providers.base import LLMResponse

    class _Provider:
        async def plan(self, *_a, **_kw):
            return LLMResponse(
                text='{"intent":"clarify","steps":[],"requires_clarification":true}',
                prompt_tokens=10, completion_tokens=2,
                latency_ms=1, model='local',
                raw={
                    'intent': 'clarify',
                    'requires_clarification': True,
                    'clarification_question': 'I need more information.',
                    'steps': [],
                },
            )

    import acme_app.application.planner as planner_mod
    monkeypatch.setattr(planner_mod, 'get_provider', lambda *_a, **_kw: _Provider())

    plan, _resp = await create_plan(
        'I have a call with Northwind today. What are the open issues, latest status, and recommended next step?',
        'ollama-llama',
        {'role': 'support_user'},
    )

    assert plan.intent == 'customer_status'
    assert plan.requires_clarification is False
    assert [step.name for step in plan.steps] == [
        'get_customer_profile',
        'get_open_issues',
        'customer_escalation_summary',
    ]
    assert all(step.arguments == {'customer_name': 'Northwind'} for step in plan.steps)


@pytest.mark.asyncio
async def test_create_plan_augments_partial_customer_status_plan(monkeypatch):
    """If Llama returns only one lookup, add the rest of the briefing steps."""
    from acme_app.infrastructure.llm.providers.base import LLMResponse

    class _Provider:
        async def plan(self, *_a, **_kw):
            return LLMResponse(
                text='{"intent":"plan_customer_specific","steps":[]}',
                prompt_tokens=10, completion_tokens=2,
                latency_ms=1, model='local',
                raw={
                    'intent': 'plan_customer_specific',
                    'requires_clarification': False,
                    'steps': [
                        {
                            'step_type': 'tool',
                            'name': 'get_open_issues',
                            'arguments': {'customer_name': 'Northwind'},
                            'rationale': 'read open issues',
                        },
                    ],
                },
            )

    import acme_app.application.planner as planner_mod
    monkeypatch.setattr(planner_mod, 'get_provider', lambda *_a, **_kw: _Provider())

    plan, _resp = await create_plan(
        'I have a call with Northwind today. What are the open issues, latest status, and recommended next step?',
        'ollama-llama',
        {'role': 'support_user'},
    )

    assert [step.name for step in plan.steps] == [
        'get_open_issues',
        'get_customer_profile',
        'customer_escalation_summary',
    ]


@pytest.mark.asyncio
async def test_create_plan_dedupes_repeated_valid_steps(monkeypatch):
    from acme_app.infrastructure.llm.providers.base import LLMResponse

    duplicated_step = {
        'step_type': 'tool',
        'name': 'get_open_issues',
        'arguments': {'customer_name': 'Northwind'},
        'rationale': 'read open issues',
    }

    class _Provider:
        async def plan(self, *_a, **_kw):
            return LLMResponse(
                text='{"intent":"status","steps":[]}',
                prompt_tokens=10, completion_tokens=2,
                latency_ms=1, model='local',
                raw={'intent': 'status', 'steps': [duplicated_step, duplicated_step]},
            )

    import acme_app.application.planner as planner_mod
    monkeypatch.setattr(planner_mod, 'get_provider', lambda *_a, **_kw: _Provider())

    plan, _resp = await create_plan('open issues for Northwind', 'ollama-llama', {'role': 'support_user'})

    names = [step.name for step in plan.steps]
    assert names.count('get_open_issues') == 1
    assert names == [
        'get_open_issues',
        'get_customer_profile',
        'customer_escalation_summary',
    ]


def test_llm_unavailable_error_is_runtime_error():
    """The orchestrator catches it via the broad except clause."""
    err = LLMUnavailableError('no llm')
    assert isinstance(err, RuntimeError)
