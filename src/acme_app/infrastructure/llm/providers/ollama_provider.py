from acme_app.infrastructure.llm.providers.base import LLMProvider


class OllamaProvider(LLMProvider):
    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        raise RuntimeError('LLM unavailable: Ollama provider is stubbed for this prototype')
