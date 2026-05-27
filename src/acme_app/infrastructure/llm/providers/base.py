from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        raise NotImplementedError
