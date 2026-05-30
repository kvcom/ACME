from acme_app.application.orchestrator import _external_llm_used


def test_external_llm_used_detects_cloud_plan_or_answer_model():
    assert _external_llm_used('gpt-5.4-mini', 'qwen3.5:9b', None)
    assert _external_llm_used('qwen3.5:9b', 'claude-sonnet-4-6', None)


def test_external_llm_used_detects_cloud_arbiter():
    assert _external_llm_used('qwen3.5:9b', 'qwen3.5:9b', 'arbiter:gpt-5.4-mini')


def test_external_llm_used_is_false_for_local_only():
    assert not _external_llm_used('qwen3.5:9b', 'qwen3.5:9b', 'rules+llama')
