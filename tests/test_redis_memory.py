import pytest

from acme_app.infrastructure.redis_memory import conversation_memory


@pytest.mark.integration
@pytest.mark.asyncio
async def test_context_roundtrip():
    await conversation_memory.append_context('test.user', 'CONV-T', {'role': 'user', 'text': 'hi'})
    ctx = await conversation_memory.get_context('test.user', 'CONV-T')
    assert ctx
    assert ctx[-1]['text'] == 'hi'


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pending_action_lifecycle():
    await conversation_memory.set_pending_action('test.user', 'CONV-T', {'action_type': 'ESCALATE_ISSUE'})
    pending = await conversation_memory.get_pending_action('test.user', 'CONV-T')
    assert pending['action_type'] == 'ESCALATE_ISSUE'
    await conversation_memory.clear_pending_action('test.user', 'CONV-T')
    after = await conversation_memory.get_pending_action('test.user', 'CONV-T')
    assert after is None
