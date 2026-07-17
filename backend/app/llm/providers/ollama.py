"""Optional local Ollama provider.

Disabled unless ``LLM_OLLAMA_ENABLED=true``. Never required for tests or CI, never
auto-downloads a model, and fails with a clear typed error when Ollama is unreachable.
Uses Ollama's native ``format: json`` for JSON-mode structured output; the service
validates the result and repairs once if needed.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.llm.cost import estimate_tokens, zero_cost
from app.llm.enums import (
    FinishReason,
    ModelErrorCode,
    ProviderCapability,
    TokenUsageSource,
)
from app.llm.models import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    TokenUsage,
)

OLLAMA_PROVIDER_NAME = "ollama"


class OllamaProvider:
    """A thin adapter over the local Ollama HTTP API."""

    provider_name = OLLAMA_PROVIDER_NAME

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        enabled: bool,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._enabled = enabled
        self._timeout = timeout_seconds

    @property
    def default_model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            capabilities=frozenset(
                {
                    ProviderCapability.JSON_MODE,
                    ProviderCapability.TOKEN_USAGE,
                    ProviderCapability.SYSTEM_MESSAGES,
                    ProviderCapability.TEMPERATURE,
                    ProviderCapability.SEED,
                }
            )
        )

    def is_available(self) -> bool:
        return self._enabled

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if not self._enabled:
            raise ModelProviderError(
                ModelErrorCode.INVALID_PROVIDER_CONFIGURATION,
                "Ollama provider is disabled (set LLM_OLLAMA_ENABLED=true).",
                provider=self.provider_name,
                retryable=False,
            )
        model = request.model or self._model
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_message},
                {"role": "user", "content": request.user_message},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": request.parameters.temperature,
                "num_predict": request.parameters.max_output_tokens,
            },
        }
        if request.parameters.seed is not None:
            body["options"]["seed"] = request.parameters.seed

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f"{self._base_url}/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise ModelProviderError(
                ModelErrorCode.PROVIDER_TIMEOUT,
                "Ollama request timed out.",
                provider=self.provider_name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelProviderError(
                ModelErrorCode.PROVIDER_UNAVAILABLE,
                f"Ollama is unreachable at {self._base_url}.",
                provider=self.provider_name,
            ) from exc

        if response.status_code >= 500:
            raise ModelProviderError(
                ModelErrorCode.PROVIDER_UNAVAILABLE,
                f"Ollama returned {response.status_code}.",
                provider=self.provider_name,
            )
        if response.status_code >= 400:
            raise ModelProviderError(
                ModelErrorCode.DEPENDENCY_UNAVAILABLE,
                f"Ollama returned {response.status_code}.",
                provider=self.provider_name,
                retryable=False,
            )

        data = response.json()
        raw_text = str(data.get("message", {}).get("content", ""))
        parsed = self._safe_parse(raw_text)
        prompt_tokens = data.get("prompt_eval_count")
        output_tokens = data.get("eval_count")
        if isinstance(prompt_tokens, int) and isinstance(output_tokens, int):
            usage = TokenUsage(
                input_tokens=prompt_tokens,
                output_tokens=output_tokens,
                source=TokenUsageSource.PROVIDER_REPORTED,
            )
        else:
            usage = TokenUsage(
                input_tokens=estimate_tokens(
                    request.system_message + request.user_message
                ),
                output_tokens=estimate_tokens(raw_text),
                source=TokenUsageSource.ESTIMATED,
            )
        return ModelResponse(
            success=True,
            raw_text=raw_text,
            parsed_output=parsed,
            provider_name=self.provider_name,
            model_name=model,
            task_type=request.task_type,
            token_usage=usage,
            cost=zero_cost(mock=False),
            latency_ms=int(data.get("total_duration", 0) // 1_000_000),
            finish_reason=FinishReason.STOP,
            retry_count=0,
        )

    @staticmethod
    def _safe_parse(raw_text: str) -> dict[str, Any] | None:
        try:
            value = json.loads(raw_text)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
