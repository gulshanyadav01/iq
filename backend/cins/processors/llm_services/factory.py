"""
LLM Service Factory

Provides a unified interface for switching between different LLM providers
(OpenAI, Gemini) based on environment configuration.

Environment variables:
- LLM_PROVIDER: Choose "openai" or "gemini" (defaults to "gemini")
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


def get_llm_provider() -> str:
    """Get the configured LLM provider from environment"""
    _load_env()
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()

    if provider not in ["openai", "gemini"]:
        logger.warning(
            f"Invalid LLM_PROVIDER '{provider}'. Defaulting to 'gemini'. "
            f"Valid options: 'openai', 'gemini'"
        )
        provider = "gemini"

    logger.info(f"Using LLM provider: {provider}")
    return provider


def get_llm_service(**kwargs):
    """
    Factory function to get the appropriate LLM service based on environment configuration.

    Returns:
        Either GeminiService or OpenAIService instance based on LLM_PROVIDER env var

    Raises:
        LLMServiceError: If the required provider library is not installed or configured

    Usage:
        # Get service based on LLM_PROVIDER env var
        service = get_llm_service()
        response = service.generate_content("Your prompt here")

        # Override with specific kwargs
        service = get_llm_service(max_retries=5)
    """
    provider = get_llm_provider()

    if provider == "openai":
        try:
            from cins.processors.llm_services.openai import OpenAIService

            logger.debug("Initializing OpenAI service")
            return OpenAIService.get_instance(**kwargs)
        except ImportError as e:
            raise LLMServiceError(
                f"OpenAI service selected but library not available: {e}. "
                "Run: pip install openai"
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


def get_config_for_extraction():
    """Get extraction configuration for the current provider"""
    provider = get_llm_provider()

    if provider == "openai":
        from cins.processors.llm_services.openai import OpenAIService

        return OpenAIService.config_for_extraction()
    else:
        from cins.processors.llm_services.gemini import GeminiService

        return GeminiService.config_for_extraction()


def get_config_for_raw_extraction():
    """Get raw extraction configuration for the current provider"""
    provider = get_llm_provider()

    if provider == "openai":
        from cins.processors.llm_services.openai import OpenAIService

        return OpenAIService.config_for_raw_extraction()
    else:
        from cins.processors.llm_services.gemini import GeminiService

        return GeminiService.config_for_raw_extraction()


def get_config_for_layout_detection():
    """Get layout detection configuration for the current provider"""
    provider = get_llm_provider()

    if provider == "openai":
        from cins.processors.llm_services.openai import OpenAIService

        return OpenAIService.config_for_layout_detection()
    else:
        from cins.processors.llm_services.gemini import GeminiService

        return GeminiService.config_for_layout_detection()


def get_config_for_sheet_classification():
    """Get sheet classification configuration for the current provider"""
    provider = get_llm_provider()

    if provider == "openai":
        from cins.processors.llm_services.openai import OpenAIService

        return OpenAIService.config_for_sheet_classification()
    else:
        from cins.processors.llm_services.gemini import GeminiService

        return GeminiService.config_for_sheet_classification()


def get_config_for_code_generation():
    """Get code-generation fallback configuration for the current provider"""
    provider = get_llm_provider()

    if provider == "openai":
        from cins.processors.llm_services.openai import OpenAIService

        return OpenAIService.config_for_code_generation()
    else:
        from cins.processors.llm_services.gemini import GeminiService

        return GeminiService.config_for_code_generation()
