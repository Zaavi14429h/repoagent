from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from repoagent.config import Settings


class ModelAdapter(ABC):
    """Converts common model configuration into a framework-specific client."""

    @abstractmethod
    def for_crewai(self): ...


@dataclass(slots=True)
class CrewAIModelAdapter(ModelAdapter):
    settings: Settings

    def for_crewai(self):
        try:
            from crewai import LLM
        except ImportError as error:
            raise RuntimeError("CrewAI is not installed") from error

        kwargs: dict[str, object] = {
            "model": self.settings.model,
            "temperature": 0.1,
        }
        if self.settings.model_api_key:
            kwargs["api_key"] = self.settings.model_api_key
        if self.settings.model_base_url:
            kwargs["base_url"] = self.settings.model_base_url
        return LLM(**kwargs)


def build_model_adapter(settings: Settings) -> ModelAdapter:
    """Single factory point for hosted, local, Qwen, DeepSeek, and Gemini models.

    CrewAI uses the model identifier and optional OpenAI-compatible base URL. This
    keeps the workflow independent from any one provider without introducing a
    third agent framework.
    """

    return CrewAIModelAdapter(settings)
