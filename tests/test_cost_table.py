from acme_app.infrastructure.llm.cost_table import estimate_cost
from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY


def test_latest_cloud_models_have_nonzero_prices():
    for key in (
        'claude-opus-4-7',
        'claude-sonnet-4-6',
        'gpt-5.5',
        'gpt-5.4-mini',
        'gemini-3.1-pro-preview',
        'gemini-3.5-flash',
    ):
        spec = MODEL_REGISTRY[key]
        assert spec.input_per_1k > 0
        assert spec.output_per_1k > 0


def test_gpt_5_5_cost_uses_current_openai_rate():
    assert estimate_cost('gpt-5.5', prompt_tokens=1000, completion_tokens=1000) == 0.035


def test_local_llama_remains_free_in_estimator():
    assert estimate_cost('ollama-llama', prompt_tokens=1000, completion_tokens=1000) == 0.0
