from acme_app.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from acme_app.infrastructure.llm.providers.base import LLMProvider
from acme_app.infrastructure.llm.providers.ollama_provider import OllamaProvider
from acme_app.infrastructure.llm.providers.openai_provider import OpenAIProvider


PROVIDERS: dict[str, type[LLMProvider]] = {
    'anthropic': AnthropicProvider,
    'openai': OpenAIProvider,
    'ollama': OllamaProvider,
}


def get_provider(name: str) -> LLMProvider:
    return PROVIDERS.get(name, AnthropicProvider)()
