"""
Centralized Gemini API Service

Provides unified interface for all Gemini API interactions in CINS.
Handles client initialization, retry logic, and configuration management.

Environment variables:
- GEMINI_API_KEY: Google API key for Gemini (required)
- GEMINI_MODEL: Model to use (required)
"""

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from pathlib import Path

from cins.processors.llm_services.factory import _load_env
from cins.processors.llm_services.base import LLMServiceMixin

logger = logging.getLogger(__name__)

# Constants
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 2  # seconds


@dataclass
class GeminiConfig:
    """Configuration for Gemini API calls"""

    temperature: float = 0
    top_p: float = 1
    max_output_tokens: Optional[int] = None
    response_mime_type: str = "application/json"
    response_json_schema: Optional[Dict] = None
    system_instruction: Optional[str] = None
    thinking_level: Optional[str] = None  # "HIGH", "MEDIUM", "LOW", None
    enable_google_search: bool = False


class GeminiServiceError(Exception):
    """Base exception for GeminiService errors"""

    pass


class GeminiAPIKeyMissingError(GeminiServiceError):
    """Raised when API key is not configured"""

    pass


class GeminiAPIError(GeminiServiceError):
    """Raised when API call fails after all retries"""

    pass


class GeminiService(LLMServiceMixin):
    """
    Centralized service for Gemini API interactions.

    Usage:
        # Simple usage with defaults
        service = GeminiService()
        response = service.generate_content("Analyze this document...")

        # With custom configuration
        config = GeminiConfig(temperature=0.3, thinking_level="HIGH")
        response = service.generate_content(prompt, config=config)

        # With image (multimodal)
        response = service.generate_content_multimodal(
            text_prompt="Describe this image",
            image=pil_image,
            config=config
        )
    """

    _instance: Optional["GeminiService"] = None

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ):
        """
        Initialize GeminiService.

        Args:
            api_key: Gemini API key. Falls back to GEMINI_API_KEY env var.
            model: Default model. Falls back to GEMINI_MODEL env var, then DEFAULT_MODEL.
            max_retries: Number of retry attempts on failure.
            retry_delay: Base delay between retries (exponential backoff).
        """
        self._api_key = api_key or self._get_api_key()
        self._model = model or self._get_default_model()
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._client = None

    @property
    def model(self) -> str:
        """Get the configured model name"""
        return self._model

    @staticmethod
    def _get_api_key() -> str:
        """Get API key from environment with multi-path .env support"""
        _load_env()

        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise GeminiAPIKeyMissingError(
                "Gemini API key not found. Set GEMINI_API_KEY environment variable."
            )
        return key

    @staticmethod
    def _get_default_model() -> str:
        """Get model from GEMINI_MODEL environment variable"""
        _load_env()
        model = os.environ.get("GEMINI_MODEL")
        if not model:
            raise GeminiServiceError(
                "Gemini model not found. Set GEMINI_MODEL environment variable."
            )
        return model

    def _get_client(self):
        """Lazy initialization of Gemini client"""
        if self._client is None:
            try:
                from google import genai

                self._client = genai.Client(api_key=self._api_key)
            except ImportError:
                raise GeminiServiceError(
                    "Google GenAI library not installed. Run: pip install google-genai"
                )
        return self._client

    def _build_config(self, config: Optional[GeminiConfig] = None):
        """Build GenerateContentConfig from GeminiConfig"""
        from google.genai import types

        config = config or GeminiConfig()

        # Build tools list
        tools = []
        if config.enable_google_search:
            tools.append(types.Tool(google_search=types.GoogleSearch()))

        # Build thinking config
        thinking_config = None
        # if config.thinking_level:
        #     thinking_config = types.ThinkingConfig(thinking_level=config.thinking_level)

        # Build system instruction
        system_instruction = None
        if config.system_instruction:
            system_instruction = [types.Part.from_text(text=config.system_instruction)]

        # Build config dict
        config_kwargs = {
            "temperature": config.temperature,
            "top_p": config.top_p,
            "response_mime_type": config.response_mime_type,
        }

        if config.max_output_tokens:
            config_kwargs["max_output_tokens"] = config.max_output_tokens
        if config.response_json_schema:
            config_kwargs["response_json_schema"] = config.response_json_schema
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if thinking_config:
            config_kwargs["thinking_config"] = thinking_config
        if tools:
            config_kwargs["tools"] = tools

        return types.GenerateContentConfig(**config_kwargs)

    def generate_content(
        self,
        prompt: str,
        config: Optional[GeminiConfig] = None,
        model: Optional[str] = None,
    ) -> str:
        """
        Generate content from text prompt.

        Args:
            prompt: Text prompt/query
            config: Generation configuration
            model: Override default model

        Returns:
            Generated text response

        Raises:
            GeminiAPIError: If all retries fail
        """
        return self._generate_with_retry(
            contents=prompt,
            config=config,
            model=model,
        )

    def generate_content_multimodal(
        self,
        text_prompt: str,
        image: Any,  # PIL.Image.Image
        config: Optional[GeminiConfig] = None,
        model: Optional[str] = None,
    ) -> str:
        """
        Generate content from text + image (multimodal).

        Args:
            text_prompt: Text prompt/query
            image: PIL Image object
            config: Generation configuration
            model: Override default model

        Returns:
            Generated text response
        """
        return self._generate_with_retry(
            contents=[text_prompt, image],
            config=config,
            model=model,
        )

    def generate_content_with_messages(
        self,
        contents: List[Any],  # List of types.Content
        config: Optional[GeminiConfig] = None,
        model: Optional[str] = None,
    ) -> str:
        """
        Generate content with structured message format.

        Args:
            contents: List of Content objects (for multi-turn or structured input)
            config: Generation configuration
            model: Override default model

        Returns:
            Generated text response
        """
        return self._generate_with_retry(
            contents=contents,
            config=config,
            model=model,
        )

    def _generate_with_retry(
        self,
        contents: Any,
        config: Optional[GeminiConfig] = None,
        model: Optional[str] = None,
    ) -> str:
        """Internal method with retry logic"""
        from cins.processors.llm_services.langfuse_client import start_generation, get_trace_context

        client = self._get_client()
        model = model or self._model
        gen_config = self._build_config(config)

        ctx = get_trace_context()
        prompt_input = config.system_instruction if (config and config.system_instruction) else (contents if isinstance(contents, str) else None)
        generation, prop_ctx = start_generation(
            name=ctx["trace_name"] or "gemini-generate",
            model=model,
            input=prompt_input,
        )

        last_error = None
        for attempt in range(self._max_retries):
            try:
                logger.debug(f"Calling Gemini API ({model}), attempt {attempt + 1}")

                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=gen_config,
                )

                text = response.text or ""

                if generation:
                    usage = getattr(response, "usage_metadata", None)
                    generation.update(
                        output=text,
                        usage_details={
                            "input": getattr(usage, "prompt_token_count", None),
                            "output": getattr(usage, "candidates_token_count", None),
                        } if usage else None,
                    )
                    generation.end()
                    prop_ctx.__exit__(None, None, None)

                return text

            except Exception as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    delay = self._retry_delay * (attempt + 1)
                    logger.warning(
                        f"Gemini API error (attempt {attempt + 1}/{self._max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Gemini API failed after {self._max_retries} attempts: {e}"
                    )

        if generation:
            generation.update(level="ERROR", status_message=str(last_error))
            generation.end()
            prop_ctx.__exit__(None, None, None)

        raise GeminiAPIError(
            f"Gemini API error after {self._max_retries} attempts: {last_error}"
        )

    # Factory methods for common configurations
    @staticmethod
    def config_for_extraction() -> GeminiConfig:
        """Configuration optimized for commission data extraction"""
        return GeminiConfig(
            temperature=0,
            top_p=1,
            thinking_level="LOW",
            enable_google_search=False,
            max_output_tokens=65535,
        )

    @staticmethod
    def config_for_raw_extraction() -> GeminiConfig:
        """Configuration for raw extraction"""
        return GeminiConfig(
            temperature=0,
            top_p=1,
            thinking_level="LOW",
            enable_google_search=False,
            max_output_tokens=65535,
        )

    @staticmethod
    def config_for_layout_detection() -> GeminiConfig:
        """Configuration for Excel layout detection"""
        return GeminiConfig(
            temperature=0.0,  # Deterministic for consistent structure detection
            top_p=0.95,
        )

    @staticmethod
    def config_for_sheet_classification() -> GeminiConfig:
        """Configuration for sheet classification"""
        return GeminiConfig(
            temperature=0.1,
            max_output_tokens=4096,
        )

    @staticmethod
    def config_for_code_generation() -> GeminiConfig:
        """Configuration for code-generation fallback (plain text output, no JSON schema)"""
        return GeminiConfig(
            temperature=0,
            top_p=1,
            thinking_level="LOW",
            enable_google_search=False,
            response_mime_type="text/plain",
            max_output_tokens=65535,
        )


# Convenience function for simple usage
def get_gemini_service(**kwargs) -> GeminiService:
    """Get or create GeminiService instance"""
    return GeminiService.get_instance(**kwargs)
