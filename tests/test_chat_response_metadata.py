from acme_app.application.orchestrator import _external_llm_used


def test_external_llm_used_detects_cloud_plan_or_answer_model():
    assert _external_llm_used('gpt-5.4-mini', 'llama3.1:8b', None)
    assert _external_llm_used('llama3.1:8b', 'claude-sonnet-4-6', None)


def test_external_llm_used_detects_cloud_arbiter():
    assert _external_llm_used('llama3.1:8b', 'llama3.1:8b', 'arbiter:gpt-5.4-mini')


def test_external_llm_used_is_false_for_local_only():
    assert not _external_llm_used('llama3.1:8b', 'llama3.1:8b', 'rules+llama')
