"""Provider-neutral request/response models, token usage, cost and error types.

These are the low-level types a :class:`~app.llm.providers.base.ModelProvider` speaks.
The higher-level service types (``ModelTaskRequest`` / ``ModelTaskResult``) live in
``app.llm.service`` and wrap these. Nothing here depends on a vendor SDK.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.llm.enums import (
    CostStatus,
    FinishReason,
    ModelErrorCode,
    ModelTaskType,
    ProviderCapability,
    TokenUsageSource,
)


class ModelParameters(BaseModel):
    """Generation parameters passed to a provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    seed: int | None = None


class TokenUsage(BaseModel):
    """Token counts with explicit provenance — never claim exact for an estimate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    source: TokenUsageSource = TokenUsageSource.UNKNOWN

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class CostEstimate(BaseModel):
    """Monetary cost in integer microunits (1 GBP = 1_000_000 microunits), GBP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    microunits: int = Field(default=0, ge=0)
    currency: str = "GBP"
    status: CostStatus = CostStatus.UNKNOWN
    price_table_version: str | None = None

    @property
    def is_known(self) -> bool:
        return self.status != CostStatus.UNKNOWN


class ProviderCapabilities(BaseModel):
    """The set of capabilities a provider declares."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capabilities: frozenset[ProviderCapability] = Field(default_factory=frozenset)

    def has(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities


class ModelError(BaseModel):
    """A typed, sanitised model error. Raw provider exceptions never reach consumers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ModelErrorCode
    message: str
    retryable: bool = False
    provider: str | None = None


class ModelRequest(BaseModel):
    """A single provider call. Rendered, redaction-ready; carries no ORM objects."""

    model_config = ConfigDict(extra="forbid")

    task_type: ModelTaskType
    model: str
    system_message: str
    user_message: str
    parameters: ModelParameters = Field(default_factory=ModelParameters)

    # Structured-output contract. The provider uses these per its capabilities.
    structured_output_schema_name: str | None = None
    structured_output_json_schema: dict[str, object] | None = None

    correlation_id: str
    # Free-form, non-sensitive trace hints. The mock provider reads scenario directives
    # from here (e.g. {"mock_scenario": "timeout"}); real providers ignore unknown keys.
    trace_metadata: dict[str, str] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    """The result of a single provider call (no fallback/repair orchestration here)."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    raw_text: str = ""
    parsed_output: dict[str, object] | None = None

    provider_name: str
    model_name: str
    task_type: ModelTaskType

    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: CostEstimate = Field(default_factory=CostEstimate)
    latency_ms: int = Field(default=0, ge=0)
    finish_reason: FinishReason = FinishReason.UNKNOWN
    provider_request_id: str | None = None
    retry_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    error: ModelError | None = None


class ModelProviderError(Exception):
    """Internal exception providers raise; the service converts it to a ``ModelError``.

    Never surfaced raw to API/tool consumers — always mapped through the taxonomy.
    """

    def __init__(
        self,
        code: ModelErrorCode,
        message: str,
        *,
        retryable: bool | None = None,
        provider: str | None = None,
    ) -> None:
        from app.llm.enums import RETRYABLE_ERROR_CODES

        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = (
            code in RETRYABLE_ERROR_CODES if retryable is None else retryable
        )
        self.provider = provider

    def as_model_error(self) -> ModelError:
        return ModelError(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            provider=self.provider,
        )
