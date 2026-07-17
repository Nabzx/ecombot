"""ModelService: the one entry point for running a model task safely.

Loads the task definition and active prompt, validates and redacts input, selects a
provider via the router, parses and semantically validates the structured output,
attempts one bounded repair on invalid output, records the call (when a session is
given) and returns a typed result. It never executes tools, runs business rules, updates
tickets or approves anything.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.llm.cost import estimate_cost, zero_cost
from app.llm.enums import (
    CostStatus,
    ModelCallStatus,
    ModelErrorCode,
    ModelTaskType,
    TokenUsageSource,
)
from app.llm.models import (
    CostEstimate,
    ModelError,
    ModelParameters,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)
from app.llm.persistence import ensure_prompt_version, record_model_call
from app.llm.providers.factory import build_providers
from app.llm.redaction import redact_json, redact_text
from app.llm.router import ProviderRouter, RoutedResult
from app.llm.schemas import OUTPUT_SCHEMAS
from app.llm.structured import StructuredOutputError, parse_and_validate
from app.llm.tasks.definitions import get_task_definition
from app.llm.tasks.semantic import SemanticContext, validate_semantics
from app.prompts.registry import PromptRegistry, get_prompt_registry
from app.prompts.renderer import render_prompt

logger = logging.getLogger("app.llm.service")


@dataclass
class ModelTaskRequest:
    """A request to run one model task. Carries pre-formatted, safe context."""

    task_type: ModelTaskType
    render_context: dict[str, str]
    semantic_context: SemanticContext = field(default_factory=SemanticContext)
    mock_payload: dict[str, object] = field(default_factory=dict)
    mock_scenario: str = ""
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ticket_id: uuid.UUID | None = None
    requested_provider: str | None = None


@dataclass
class ModelTaskResult:
    """The typed outcome of a model task."""

    success: bool
    task_type: ModelTaskType
    correlation_id: str
    output: BaseModel | None = None
    provider: str = ""
    model: str = ""
    requested_provider: str = ""
    fallback_from: str | None = None
    fallback_reason: str | None = None
    attempts: int = 0
    prompt_name: str = ""
    prompt_version: str = ""
    prompt_hash: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    cost: CostEstimate = field(default_factory=CostEstimate)
    latency_ms: int = 0
    repair_count: int = 0
    warnings: list[str] = field(default_factory=list)
    error: ModelError | None = None


class ModelService:
    """High-level orchestration of a single model task."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        registry: PromptRegistry | None = None,
        router: ProviderRouter | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or get_prompt_registry()
        providers = build_providers(self._settings)
        self._router = router or ProviderRouter(
            providers,
            fallback_order=self._settings.llm_fallback_order,
            max_attempts=self._settings.llm_max_retries,
            request_timeout=self._settings.llm_request_timeout_seconds,
            total_deadline=self._settings.llm_total_deadline_seconds,
            fallback_enabled=self._settings.llm_fallback_enabled,
        )

    async def run_task(
        self,
        request: ModelTaskRequest,
        *,
        session: AsyncSession | None = None,
    ) -> ModelTaskResult:
        started_at = datetime.now(UTC)
        definition = get_task_definition(request.task_type)
        prompt = self._registry.active_for_task(request.task_type)
        schema = self._output_schema(request.task_type)

        redacted_input = redact_json(dict(request.render_context))
        input_hash = _hash_json(redacted_input)

        # Enforce the input bound before any provider work.
        total_chars = sum(len(v) for v in request.render_context.values())
        if total_chars > definition.max_input_chars:
            return self._failure(
                request,
                definition_prompt=(
                    prompt.name,
                    prompt.semantic_version,
                    prompt.template_hash,
                ),
                started_at=started_at,
                error=ModelError(
                    code=ModelErrorCode.INPUT_TOO_LONG,
                    message=(
                        f"input {total_chars} chars exceeds "
                        f"{definition.max_input_chars}"
                    ),
                ),
                session=session,
                redacted_input=redacted_input,
                input_hash=input_hash,
            )

        rendered = render_prompt(prompt, dict(request.render_context))
        params = ModelParameters(
            temperature=prompt.default_temperature,
            max_output_tokens=min(
                definition.max_output_tokens, self._settings.llm_max_output_tokens
            ),
            timeout_seconds=self._settings.llm_request_timeout_seconds,
        )
        model_request = ModelRequest(
            task_type=request.task_type,
            model=self._settings.llm_default_model,
            system_message=rendered.system_message,
            user_message=rendered.user_message,
            parameters=params,
            structured_output_schema_name=prompt.output_schema_name,
            structured_output_json_schema=(
                schema.model_json_schema() if schema else None
            ),
            correlation_id=request.correlation_id,
            trace_metadata=self._trace_metadata(request),
        )

        try:
            routed = await self._router.route(
                model_request,
                requested_provider=request.requested_provider,
                fallback_allowed=definition.retry_policy.fallback_allowed,
            )
        except ModelProviderError as exc:
            return self._failure(
                request,
                definition_prompt=(
                    prompt.name,
                    prompt.semantic_version,
                    prompt.template_hash,
                ),
                started_at=started_at,
                error=exc.as_model_error(),
                session=session,
                redacted_input=redacted_input,
                input_hash=input_hash,
            )

        warnings = list(request.semantic_context.warnings)
        warnings.extend(routed.response.warnings)
        repair_count = 0

        # Parse + validate; one bounded repair attempt on invalid output.
        try:
            output = self._parse_validate(routed.response, schema, request)
        except StructuredOutputError as first_error:
            repaired = await self._attempt_repair(request, routed, schema, first_error)
            if repaired is None:
                return self._failure(
                    request,
                    definition_prompt=(
                        prompt.name,
                        prompt.semantic_version,
                        prompt.template_hash,
                    ),
                    started_at=started_at,
                    error=ModelError(
                        code=ModelErrorCode.OUTPUT_REPAIR_FAILED,
                        message="output invalid and repair produced no valid output",
                    ),
                    session=session,
                    redacted_input=redacted_input,
                    input_hash=input_hash,
                    routed=routed,
                )
            output, routed = repaired
            repair_count = 1

        parsed_json = output.model_dump(mode="json")
        cost = self._cost_for(routed.response)
        result = ModelTaskResult(
            success=True,
            task_type=request.task_type,
            correlation_id=request.correlation_id,
            output=output,
            provider=routed.actual_provider,
            model=routed.response.model_name,
            requested_provider=routed.requested_provider,
            fallback_from=routed.fallback_from,
            fallback_reason=routed.fallback_reason,
            attempts=routed.attempts,
            prompt_name=prompt.name,
            prompt_version=prompt.semantic_version,
            prompt_hash=prompt.template_hash,
            token_usage=routed.response.token_usage,
            cost=cost,
            latency_ms=routed.response.latency_ms,
            repair_count=repair_count,
            warnings=warnings,
        )
        await self._persist(
            request,
            prompt=prompt,
            status=(
                ModelCallStatus.REPAIRED if repair_count else ModelCallStatus.SUCCEEDED
            ),
            routed=routed,
            cost=cost,
            repair_count=repair_count,
            redacted_input=redacted_input,
            input_hash=input_hash,
            parsed_json=parsed_json,
            started_at=started_at,
            session=session,
            error=None,
        )
        self._log(result)
        return result

    # -- internals ------------------------------------------------------------------
    @staticmethod
    def _output_schema(task_type: ModelTaskType) -> type[BaseModel] | None:
        definition = get_task_definition(task_type)
        if definition.output_schema_name is None:
            return None
        return OUTPUT_SCHEMAS.get(definition.output_schema_name)

    def _trace_metadata(self, request: ModelTaskRequest) -> dict[str, str]:
        metadata = {"mock_payload": json.dumps(request.mock_payload, sort_keys=True)}
        if request.mock_scenario:
            metadata["mock_scenario"] = request.mock_scenario
        return metadata

    def _parse_validate(
        self,
        response: ModelResponse,
        schema: type[BaseModel] | None,
        request: ModelTaskRequest,
    ) -> BaseModel:
        if schema is None:  # pragma: no cover - repair task has no direct schema
            raise StructuredOutputError(
                ModelErrorCode.INTERNAL_ERROR, "task has no output schema"
            )
        parsed = parse_and_validate(response.raw_text, schema)
        return validate_semantics(request.task_type, parsed, request.semantic_context)

    async def _attempt_repair(
        self,
        request: ModelTaskRequest,
        routed: RoutedResult,
        schema: type[BaseModel] | None,
        error: StructuredOutputError,
    ) -> tuple[BaseModel, RoutedResult] | None:
        """One controlled repair call. Returns (output, routed) or None on failure."""
        if schema is None:
            return None
        repair_prompt = self._registry.active_for_task(
            ModelTaskType.STRUCTURED_OUTPUT_REPAIR
        )
        context = {
            "task_name": request.task_type.value,
            "output_schema": json.dumps(schema.model_json_schema()),
            "invalid_output": routed.response.raw_text[:4000],
            "validation_errors": "; ".join(error.errors) or error.message,
        }
        rendered = render_prompt(repair_prompt, context)
        repair_request = ModelRequest(
            task_type=ModelTaskType.STRUCTURED_OUTPUT_REPAIR,
            model=self._settings.llm_default_model,
            system_message=rendered.system_message,
            user_message=rendered.user_message,
            parameters=ModelParameters(
                max_output_tokens=self._settings.llm_max_output_tokens
            ),
            correlation_id=request.correlation_id,
            trace_metadata={
                "mock_payload": json.dumps(
                    {
                        "target_task": request.task_type.value,
                        "original_payload": request.mock_payload,
                    },
                    sort_keys=True,
                ),
                "mock_scenario": (
                    "repair_fail" if request.mock_scenario == "repair_fail" else ""
                ),
            },
        )
        try:
            repair_routed = await self._router.route(
                repair_request,
                requested_provider=routed.actual_provider,
                fallback_allowed=False,
            )
            output = self._parse_validate(repair_routed.response, schema, request)
        except (StructuredOutputError, ModelProviderError):
            return None
        return output, repair_routed

    def _cost_for(self, response: ModelResponse) -> CostEstimate:
        if response.provider_name == "mock":
            return zero_cost(mock=True)
        if response.provider_name == "ollama":
            return zero_cost(mock=False)
        if response.token_usage.source == TokenUsageSource.UNKNOWN:
            return CostEstimate(status=CostStatus.UNKNOWN)
        return estimate_cost(
            model=response.model_name,
            usage=response.token_usage,
            price_table_version=self._settings.llm_cost_table_version,
        )

    def _failure(
        self,
        request: ModelTaskRequest,
        *,
        definition_prompt: tuple[str, str, str],
        started_at: datetime,
        error: ModelError,
        session: AsyncSession | None,
        redacted_input: dict[str, object],
        input_hash: str,
        routed: RoutedResult | None = None,
    ) -> ModelTaskResult:
        name, version, prompt_hash = definition_prompt
        result = ModelTaskResult(
            success=False,
            task_type=request.task_type,
            correlation_id=request.correlation_id,
            prompt_name=name,
            prompt_version=version,
            prompt_hash=prompt_hash,
            provider=routed.actual_provider if routed else "",
            error=error,
            warnings=list(request.semantic_context.warnings),
        )
        self._log(result)
        return result

    async def _persist(
        self,
        request: ModelTaskRequest,
        *,
        prompt: object,
        status: ModelCallStatus,
        routed: RoutedResult,
        cost: CostEstimate,
        repair_count: int,
        redacted_input: dict[str, object],
        input_hash: str,
        parsed_json: dict[str, object] | None,
        started_at: datetime,
        session: AsyncSession | None,
        error: ModelError | None,
    ) -> None:
        if session is None or not self._settings.llm_prompt_persistence_enabled:
            return
        from app.prompts.models import PromptDefinition  # local import for typing

        assert isinstance(prompt, PromptDefinition)  # noqa: S101 - narrow type
        prompt_row = await ensure_prompt_version(session, prompt)
        raw_out = (
            redact_text(routed.response.raw_text)
            if self._settings.llm_raw_output_persistence_enabled
            else None
        )
        await record_model_call(
            session,
            ticket_id=request.ticket_id,
            task_type=request.task_type,
            provider=routed.actual_provider,
            model=routed.response.model_name,
            prompt_version_id=prompt_row.id,
            correlation_id=request.correlation_id,
            status=status,
            input_token_count=routed.response.token_usage.input_tokens,
            output_token_count=routed.response.token_usage.output_tokens,
            token_source=routed.response.token_usage.source.value,
            estimated_cost_microunits=cost.microunits,
            cost_status=cost.status.value,
            latency_ms=routed.response.latency_ms,
            finish_reason=routed.response.finish_reason.value,
            repair_count=repair_count,
            fallback_from_provider=routed.fallback_from,
            fallback_reason=routed.fallback_reason,
            input_hash=input_hash,
            output_hash=_hash_json(parsed_json) if parsed_json else None,
            redacted_input_json=redacted_input,
            parsed_output_json=parsed_json,
            raw_output_redacted=raw_out,
            error_code=error.code.value if error else None,
            error_message=error.message if error else None,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    @staticmethod
    def _log(result: ModelTaskResult) -> None:
        # Safe fields only — never the prompt text, customer content or secrets.
        logger.info(
            "model_task",
            extra={
                "task": result.task_type.value,
                "correlation_id": result.correlation_id,
                "prompt": f"{result.prompt_name}@{result.prompt_version}",
                "provider": result.provider,
                "requested_provider": result.requested_provider,
                "fallback_from": result.fallback_from,
                "attempts": result.attempts,
                "repair_count": result.repair_count,
                "input_tokens": result.token_usage.input_tokens,
                "output_tokens": result.token_usage.output_tokens,
                "cost_status": result.cost.status.value,
                "latency_ms": result.latency_ms,
                "success": result.success,
                "error_code": result.error.code.value if result.error else None,
            },
        )


def _hash_json(payload: dict[str, object] | None) -> str:
    canonical = json.dumps(payload or {}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
