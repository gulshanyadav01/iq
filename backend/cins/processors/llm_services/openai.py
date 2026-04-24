"""
OpenAI Service

Provides unified interface for OpenAI API interactions in CINS.
Handles client initialization, retry logic, and configuration management.

Environment variables:
- OPENAI_API_KEY: OpenAI API key (required)
- OPENAI_MODEL: Model to use (required)
"""

import os
import time
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from pathlib import Path

from cins.processors.llm_services.factory import _load_env
from cins.processors.llm_services.base import LLMServiceMixin

logger = logging.getLogger(__name__)

# Constants
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 2  # seconds

# Reasoning models that don't support temperature, top_p, and other sampling parameters
# See: https://platform.openai.com/docs/guides/reasoning
REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")
UNSUPPORTED_PARAMS_FOR_REASONING = (
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
    "logprobs",
    "top_logprobs",
    "logit_bias",
)


@dataclass
class OpenAIConfig:
    """Configuration for OpenAI API calls"""

    temperature: float = 1.0
    top_p: float = 0.95
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    response_format: Optional[Dict[str, Any]] = None
    system_instruction: Optional[str] = None


class OpenAIServiceError(Exception):
    """Base exception for OpenAIService errors"""

    pass


class OpenAIAPIKeyMissingError(OpenAIServiceError):
    """Raised when API key is not configured"""

    pass


class OpenAIAPIError(OpenAIServiceError):
    """Raised when API call fails after all retries"""

    pass


class OpenAIService(LLMServiceMixin):
    """
    Centralized service for OpenAI API interactions.

    Usage:
        # Simple usage with defaults
        service = OpenAIService()
        response = service.generate_content("Analyze this document...")

        # With custom configuration
        config = OpenAIConfig(temperature=0.3)
        response = service.generate_content(prompt, config=config)
    """

    _instance: Optional["OpenAIService"] = None

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ):
        """
        Initialize OpenAIService.

        Args:
            api_key: OpenAI API key. Falls back to OPENAI_API_KEY env var.
            model: Default model. Falls back to OPENAI_MODEL env var.
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

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise OpenAIAPIKeyMissingError(
                "OpenAI API key not found. Set OPENAI_API_KEY environment variable."
            )
        return key

    @staticmethod
    def _get_default_model() -> str:
        """Get model from OPENAI_MODEL environment variable"""
        _load_env()
        model = os.environ.get("OPENAI_MODEL")
        if not model:
            raise OpenAIServiceError(
                "OpenAI model not found. Set OPENAI_MODEL environment variable."
            )
        return model

    def _get_client(self):
        """Lazy initialization of OpenAI client"""
        if self._client is None:
            try:
                from openai import OpenAI

                self._client = OpenAI(api_key=self._api_key)
            except ImportError:
                raise OpenAIServiceError(
                    "OpenAI library not installed. Run: pip install openai"
                )
        return self._client

    @staticmethod
    def _is_reasoning_model(model: str) -> bool:
        """Check if model is a reasoning model that doesn't support sampling parameters."""
        return model.startswith(REASONING_MODEL_PREFIXES)

    @staticmethod
    def _remove_unsupported_params_for_reasoning(
        request_kwargs: Dict[str, Any], model: str
    ) -> Dict[str, Any]:
        """Remove unsupported parameters for reasoning models."""
        if not OpenAIService._is_reasoning_model(model):
            return request_kwargs

        for param in UNSUPPORTED_PARAMS_FOR_REASONING:
            if param in request_kwargs:
                del request_kwargs[param]
                logger.debug(f"Removed unsupported param '{param}' for reasoning model: {model}")

        return request_kwargs

    def generate_content(
        self,
        prompt: str,
        config: Optional[OpenAIConfig] = None,
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
            OpenAIAPIError: If all retries fail
        """
        client = self._get_client()
        model = model or self._model
        config = config or OpenAIConfig()

        # Build messages
        messages = []
        if config.system_instruction:
            messages.append({"role": "system", "content": config.system_instruction})
        messages.append({"role": "user", "content": prompt})

        # Build request kwargs
        request_kwargs = {
            "model": model,
            "messages": messages,
            "temperature": config.temperature,
            "top_p": config.top_p,
        }

        # Handle max tokens - use max_completion_tokens (preferred) or max_tokens
        completion_tokens = config.max_completion_tokens or config.max_tokens
        if completion_tokens:
            request_kwargs["max_completion_tokens"] = completion_tokens

        # Explicitly ensure max_tokens is NOT in kwargs
        if "max_tokens" in request_kwargs:
            del request_kwargs["max_tokens"]

        # Remove unsupported parameters for reasoning models (o1, o3, o4, gpt-5)
        request_kwargs = self._remove_unsupported_params_for_reasoning(request_kwargs, model)

        if config.response_format:
            request_kwargs["response_format"] = config.response_format

        return self._generate_with_retry(client, request_kwargs)

    def generate_content_multimodal(
        self,
        text_prompt: str,
        image: Any,  # PIL.Image.Image
        config: Optional[OpenAIConfig] = None,
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
        import base64
        import io

        client = self._get_client()
        model = model or self._model
        config = config or OpenAIConfig()

        # Convert PIL image to base64
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()

        # Build messages
        messages = []
        if config.system_instruction:
            messages.append({"role": "system", "content": config.system_instruction})

        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_base64}"},
                    },
                ],
            }
        )

        # Build request kwargs
        request_kwargs = {
            "model": model,
            "messages": messages,
            "temperature": config.temperature,
            "top_p": config.top_p,
        }

        if config.max_completion_tokens:
            request_kwargs["max_completion_tokens"] = config.max_completion_tokens
        elif config.max_tokens:
            request_kwargs["max_completion_tokens"] = config.max_tokens

        # Remove unsupported parameters for reasoning models (o1, o3, o4, gpt-5)
        request_kwargs = self._remove_unsupported_params_for_reasoning(request_kwargs, model)

        return self._generate_with_retry(client, request_kwargs)

    def _generate_with_retry(self, client, request_kwargs: Dict[str, Any]) -> str:
        """Internal method with retry logic"""
        from cins.processors.llm_services.langfuse_client import start_generation, get_trace_context

        ctx = get_trace_context()
        messages = request_kwargs.get("messages", [])
        prompt_input = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "system"),
            messages[-1]["content"] if messages else None,
        )
        generation, prop_ctx = start_generation(
            name=ctx["trace_name"] or "openai-generate",
            model=request_kwargs["model"],
            input=prompt_input,
        )

        last_error = None
        for attempt in range(self._max_retries):
            try:
                logger.debug(
                    f"Calling OpenAI API ({request_kwargs['model']}), attempt {attempt + 1}"
                )

                response = client.chat.completions.create(**request_kwargs)
                text = response.choices[0].message.content or ""

                if generation:
                    usage = getattr(response, "usage", None)
                    generation.update(
                        output=text,
                        usage_details={
                            "input": getattr(usage, "prompt_tokens", None),
                            "output": getattr(usage, "completion_tokens", None),
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
                        f"OpenAI API error (attempt {attempt + 1}/{self._max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"OpenAI API failed after {self._max_retries} attempts: {e}"
                    )

        if generation:
            generation.update(level="ERROR", status_message=str(last_error))
            generation.end()
            prop_ctx.__exit__(None, None, None)

        raise OpenAIAPIError(
            f"OpenAI API error after {self._max_retries} attempts: {last_error}"
        )

    # Factory methods for common configurations
    @staticmethod
    def config_for_extraction() -> OpenAIConfig:
        """Configuration optimized for commission data extraction"""
        return OpenAIConfig(
            temperature=0.3,
            top_p=0.95,
            max_completion_tokens=16000,
        )

    @staticmethod
    def config_for_raw_extraction() -> OpenAIConfig:
        """Configuration for raw extraction"""
        return OpenAIConfig(
            temperature=0.3,
            top_p=0.95,
            max_completion_tokens=16000,
        )

    @staticmethod
    def config_for_layout_detection() -> OpenAIConfig:
        """Configuration for Excel layout detection"""
        return OpenAIConfig(
            temperature=0.0,  # Deterministic for consistent structure detection
            top_p=0.95,
        )

    @staticmethod
    def config_for_sheet_classification() -> OpenAIConfig:
        """Configuration for sheet classification"""
        return OpenAIConfig(
            temperature=0.1,
            max_completion_tokens=4096,
        )

    @staticmethod
    def config_for_code_generation() -> OpenAIConfig:
        """Configuration for code-generation fallback (plain text output, no JSON mode)"""
        return OpenAIConfig(
            temperature=0.2,
            top_p=0.95,
            response_format=None,
            max_completion_tokens=16000,
        )


# Convenience function for simple usage
def get_openai_service(**kwargs) -> OpenAIService:
    """Get or create OpenAIService instance"""
    return OpenAIService.get_instance(**kwargs)
