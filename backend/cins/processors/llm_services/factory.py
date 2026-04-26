"""
LLM Service Factory

Provides a unified interface for switching between different LLM providers
(OpenAI, Gemini, Anthropic) based on environment configuration.

Environment variables:
- LLM_PROVIDER: Choose "openai", "gemini", or "anthropic" (defaults to "gemini")
"""

import os
import logging
from typing import Optional, Union
from pathlib import Path

logger = logging.getLogger(__name__)


class LLMServiceError(Exception):
    """Base exception for LLM service errors"""

    pass


def _load_env():
    """Load environment variables from .env files"""
    try:
        from dotenv import load_dotenv

        for env_path in [
            Path(__file__).parent.parent / "streamlit_app" / ".env",
            Path(__file__).parent.parent / ".env",
            Path(__file__).parent / ".env",
        ]:
            if env_path.exists():
                load_dotenv(env_path)
                break
    except ImportError:
        pass  # dotenv not installed, rely on system env


VALID_PROVIDERS = ["openai", "gemini", "anthropic"]


def get_llm_provider(provider: Optional[str] = None) -> str:
    """Get the LLM provider. Explicit argument takes precedence over LLM_PROVIDER env var."""
    if provider:
        p = provider.lower()
        if p in VALID_PROVIDERS:
            return p
        logger.warning(f"Invalid llm_provider '{p}'. Defaulting to 'gemini'.")
        return "gemini"

    _load_env()
    p = os.environ.get("LLM_PROVIDER", "gemini").lower()
    if p not in VALID_PROVIDERS:
        logger.warning(
            f"Invalid LLM_PROVIDER '{p}'. Defaulting to 'gemini'. "
            f"Valid options: {', '.join(VALID_PROVIDERS)}"
        )
        p = "gemini"

    logger.info(f"Using LLM provider: {p}")
    return p


def get_llm_service(provider: Optional[str] = None, **kwargs):
    """
    Factory function to get the appropriate LLM service.

    Args:
        provider: Explicit provider override ("openai" or "gemini").
                  Falls back to LLM_PROVIDER env var if not given.
    """
    resolved = get_llm_provider(provider)

    if resolved == "openai":
        try:
            from cins.processors.llm_services.openai import OpenAIService

            logger.debug("Initializing OpenAI service")
            return OpenAIService.get_instance(**kwargs)
        except ImportError as e:
            raise LLMServiceError(
                f"OpenAI service selected but library not available: {e}. "
                "Run: pip install openai"
            )
    elif resolved == "anthropic":
        try:
            from cins.processors.llm_services.anthropic import AnthropicService

            logger.debug("Initializing Anthropic service")
            return AnthropicService.get_instance(**kwargs)
        except ImportError as e:
            raise LLMServiceError(
                f"Anthropic service selected but library not available: {e}. "
                "Run: pip install anthropic"
            )
    else:  # gemini
        try:
            from cins.processors.llm_services.gemini import GeminiService

            logger.debug("Initializing Gemini service")
            return GeminiService.get_instance(**kwargs)
        except ImportError as e:
            raise LLMServiceError(
                f"Gemini service selected but library not available: {e}. "
                "Run: pip install google-genai"
            )


def _get_service_class(resolved: str):
    if resolved == "openai":
        from cins.processors.llm_services.openai import OpenAIService
        return OpenAIService
    elif resolved == "anthropic":
        from cins.processors.llm_services.anthropic import AnthropicService
        return AnthropicService
    else:
        from cins.processors.llm_services.gemini import GeminiService
        return GeminiService


def get_config_for_extraction(provider: Optional[str] = None):
    """Get extraction configuration for the given provider (or env default)."""
    return _get_service_class(get_llm_provider(provider)).config_for_extraction()


def get_config_for_raw_extraction(provider: Optional[str] = None):
    """Get raw extraction configuration for the given provider (or env default)."""
    return _get_service_class(get_llm_provider(provider)).config_for_raw_extraction()


def get_config_for_layout_detection(provider: Optional[str] = None):
    """Get layout detection configuration for the given provider (or env default)."""
    return _get_service_class(get_llm_provider(provider)).config_for_layout_detection()


def get_config_for_sheet_classification(provider: Optional[str] = None):
    """Get sheet classification configuration for the given provider (or env default)."""
    return _get_service_class(get_llm_provider(provider)).config_for_sheet_classification()


def get_config_for_code_generation(provider: Optional[str] = None):
    """Get code-generation fallback configuration for the given provider (or env default)."""
    return _get_service_class(get_llm_provider(provider)).config_for_code_generation()
