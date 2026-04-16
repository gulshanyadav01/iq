"""
LLM Service Base Mixin

Provides the shared singleton pattern (get_instance / reset_instance)
used by both GeminiService and OpenAIService.
"""

from typing import Optional, TypeVar

T = TypeVar("T", bound="LLMServiceMixin")


class LLMServiceMixin:
    """
    Mixin providing a shared singleton pattern for LLM service classes.

    Subclasses get get_instance() / reset_instance() for free.
    Each subclass must declare its own class-level _instance = None
    so instances are not shared across providers.
    """

    _instance: Optional["LLMServiceMixin"] = None

    @classmethod
    def get_instance(cls: type[T], **kwargs) -> T:
        """
        Get singleton instance (optional pattern for shared state).

        Use this when you want to share a single client across multiple calls.
        """
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (useful for testing)"""
        cls._instance = None
