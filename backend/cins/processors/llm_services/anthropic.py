"""
Anthropic Service

Provides unified interface for Anthropic (Claude) API interactions in CINS.
Handles client initialization, retry logic, and configuration management.

Environment variables:
- ANTHROPIC_API_KEY: Anthropic API key (required)
- ANTHROPIC_MODEL: Model to use (required, e.g. claude-sonnet-4-6)
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

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 2  # seconds


@dataclass
class AnthropicConfig:
    """Configuration for Anthropic API calls"""

    temperature: float = 1.0
    top_p: float = 0.95
    max_tokens: int = 8096
    system_instruction: Optional[str] = None


class AnthropicServiceError(Exception):
    pass


class AnthropicAPIKeyMissingError(AnthropicServiceError):
    pass


class AnthropicAPIError(AnthropicServiceError):
    pass


class AnthropicService(LLMServiceMixin):
    _instance: Optional["AnthropicService"] = None

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ):
        self._api_key = api_key or self._get_api_key()
        self._model = model or self._get_default_model()
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._client = None

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def _get_api_key() -> str:
        _load_env()
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise AnthropicAPIKeyMissingError(
                "Anthropic API key not found. Set ANTHROPIC_API_KEY environment variable."
            )
        return key

    @staticmethod
    def _get_default_model() -> str:
        _load_env()
        model = os.environ.get("ANTHROPIC_MODEL")
        if not model:
            raise AnthropicServiceError(
                "Anthropic model not found. Set ANTHROPIC_MODEL environment variable."
            )
        return model

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic

                self._client = anthropic.Anthropic(api_key=self._api_key)
            except ImportError:
                raise AnthropicServiceError(
                    "Anthropic library not installed. Run: pip install anthropic"
                )
        return self._client

    def generate_content(
        self,
        prompt: str,
        config: Optional[AnthropicConfig] = None,
        model: Optional[str] = None,
    ) -> str:
        client = self._get_client()
        model = model or self._model
        config = config or AnthropicConfig()

        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "top_p": config.top_p,
            "messages": [{"role": "user", "content": prompt}],
        }
        if config.system_instruction:
            kwargs["system"] = config.system_instruction

        return self._generate_with_retry(client, kwargs)

    def generate_content_multimodal(
        self,
        text_prompt: str,
        image: Any,  # PIL.Image.Image
        config: Optional[AnthropicConfig] = None,
        model: Optional[str] = None,
    ) -> str:
        import base64
        import io

        client = self._get_client()
        model = model or self._model
        config = config or AnthropicConfig()

        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()

        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "top_p": config.top_p,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": img_base64,
                            },
                        },
                        {"type": "text", "text": text_prompt},
                    ],
                }
            ],
        }
        if config.system_instruction:
            kwargs["system"] = config.system_instruction

        return self._generate_with_retry(client, kwargs)

    def _generate_with_retry(self, client, kwargs: Dict[str, Any]) -> str:
        from cins.processors.llm_services.langfuse_client import start_generation, get_trace_context

        ctx = get_trace_context()
        messages = kwargs.get("messages", [])
        prompt_input = messages[-1]["content"] if messages else None
        generation, prop_ctx = start_generation(
            name=ctx["trace_name"] or "anthropic-generate",
            model=kwargs["model"],
            input=prompt_input,
        )

        last_error = None
        for attempt in range(self._max_retries):
            try:
                logger.debug(f"Calling Anthropic API ({kwargs['model']}), attempt {attempt + 1}")

                response = client.messages.create(**kwargs)
                text = response.content[0].text if response.content else ""

                if generation:
                    usage = getattr(response, "usage", None)
                    generation.update(
                        output=text,
                        usage_details={
                            "input": getattr(usage, "input_tokens", None),
                            "output": getattr(usage, "output_tokens", None),
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
                        f"Anthropic API error (attempt {attempt + 1}/{self._max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Anthropic API failed after {self._max_retries} attempts: {e}")

        if generation:
            generation.update(level="ERROR", status_message=str(last_error))
            generation.end()
            prop_ctx.__exit__(None, None, None)

        raise AnthropicAPIError(
            f"Anthropic API error after {self._max_retries} attempts: {last_error}"
        )

    @staticmethod
    def config_for_extraction() -> AnthropicConfig:
        # temperature=0 for rule-count stability across reruns. Matches the
        # Gemini extraction configs. Residual serving-side jitter remains.
        return AnthropicConfig(temperature=0, top_p=0.95, max_tokens=16000)

    @staticmethod
    def config_for_raw_extraction() -> AnthropicConfig:
        return AnthropicConfig(temperature=0, top_p=0.95, max_tokens=16000)

    @staticmethod
    def config_for_layout_detection() -> AnthropicConfig:
        return AnthropicConfig(temperature=0.0, top_p=0.95, max_tokens=4096)

    @staticmethod
    def config_for_sheet_classification() -> AnthropicConfig:
        return AnthropicConfig(temperature=0.1, max_tokens=4096)

    @staticmethod
    def config_for_code_generation() -> AnthropicConfig:
        # Codegen needs deterministic Python output; LLM jitter at >0 produces
        # different parser code each run and inflates rule-count variance.
        return AnthropicConfig(temperature=0, top_p=0.95, max_tokens=16000)
