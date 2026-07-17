"""Optional hosted, OpenAI-compatible provider adapter.

Disabled by default; enabled with ``LLM_HOSTED_ENABLED=true`` and configured entirely
from environment variables. No API key is ever committed, logged or returned. Selecting
it without a key fails with a clear typed error. Not required for tests or CI.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.llm.cost import estimate_cost, estimate_tokens
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

HOSTED_PROVIDER_NAME = "hosted"


class HostedProvider:
    """OpenAI-compatible ``/chat/completions`` adapter (kept generic on purpose)."""

    provider_name = HOSTED_PROVIDER_NAME

    def __init__(
        self,
        *,
        base_url: str | None,
        model: str,
        api_key: str | None,
        enabled: bool,
        price_table_version: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._model = model
        self._api_key = api_key
        self._enabled = enabled
        self._price_table_version = price_table_version
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
                    ProviderCapability.REQUEST_IDS,
                    ProviderCapability.SYSTEM_MESSAGES,
                    ProviderCapability.TEMPERATURE,
                    ProviderCapability.SEED,
                }
            )
        )

    def is_available(self) -> bool:
        return self._enabled and bool(self._api_key) and bool(self._base_url)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if not self._enabled:
            raise ModelProviderError(
                ModelErrorCode.INVALID_PROVIDER_CONFIGURATION,
                "Hosted provider is disabled (set LLM_HOSTED_ENABLED=true).",
                provider=self.provider_name,
                retryable=False,
            )
        if not self._api_key:
            raise ModelProviderError(
                ModelErrorCode.PROVIDER_AUTHENTICATION_FAILED,
                "Hosted provider selected but no API key is configured.",
                provider=self.provider_name,
                retryable=False,
            )
        if not self._base_url:
            raise ModelProviderError(
                ModelErrorCode.INVALID_PROVIDER_CONFIGURATION,
                "Hosted provider selected but no base URL is configured.",
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
            "temperature": request.parameters.temperature,
            "max_tokens": request.parameters.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        if request.parameters.seed is not None:
            body["seed"] = request.parameters.seed
        headers = {"Authorization": f"Bearer {self._api_key}"}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions", json=body, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise ModelProviderError(
                ModelErrorCode.PROVIDER_TIMEOUT,
                "Hosted provider request timed out.",
                provider=self.provider_name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelProviderError(
                ModelErrorCode.PROVIDER_UNAVAILABLE,
                "Hosted provider is unreachable.",
                provider=self.provider_name,
            ) from exc

        self._raise_for_status(response.status_code)
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        raw_text = str(choice.get("message", {}).get("content", ""))
        finish = choice.get("finish_reason")
        usage = self._usage_from(data, request, raw_text)
        cost = estimate_cost(
            model=model,
            usage=usage,
            price_table_version=self._price_table_version,
        )
        return ModelResponse(
            success=True,
            raw_text=raw_text,
            parsed_output=self._safe_parse(raw_text),
            provider_name=self.provider_name,
            model_name=model,
            task_type=request.task_type,
            token_usage=usage,
            cost=cost,
            latency_ms=0,
            finish_reason=(
                FinishReason.LENGTH if finish == "length" else FinishReason.STOP
            ),
            provider_request_id=data.get("id"),
            retry_count=0,
        )

    def _raise_for_status(self, status_code: int) -> None:
        if status_code == 401 or status_code == 403:
            raise ModelProviderError(
                ModelErrorCode.PROVIDER_AUTHENTICATION_FAILED,
                "Hosted provider rejected the credentials.",
                provider=self.provider_name,
                retryable=False,
            )
        if status_code == 429:
            raise ModelProviderError(
                ModelErrorCode.PROVIDER_RATE_LIMITED,
                "Hosted provider rate limited the request.",
                provider=self.provider_name,
            )
        if status_code >= 500:
            raise ModelProviderError(
                ModelErrorCode.PROVIDER_UNAVAILABLE,
                f"Hosted provider returned {status_code}.",
                provider=self.provider_name,
            )
        if status_code >= 400:
            raise ModelProviderError(
                ModelErrorCode.DEPENDENCY_UNAVAILABLE,
                f"Hosted provider returned {status_code}.",
                provider=self.provider_name,
                retryable=False,
            )

    @staticmethod
    def _usage_from(
        data: dict[str, Any], request: ModelRequest, raw_text: str
    ) -> TokenUsage:
        usage = data.get("usage") or {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if isinstance(prompt, int) and isinstance(completion, int):
            return TokenUsage(
                input_tokens=prompt,
                output_tokens=completion,
                source=TokenUsageSource.PROVIDER_REPORTED,
            )
        return TokenUsage(
            input_tokens=estimate_tokens(request.system_message + request.user_message),
            output_tokens=estimate_tokens(raw_text),
            source=TokenUsageSource.ESTIMATED,
        )

    @staticmethod
    def _safe_parse(raw_text: str) -> dict[str, Any] | None:
        try:
            value = json.loads(raw_text)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
