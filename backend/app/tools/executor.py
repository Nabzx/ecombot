"""Tool executor: validates input, enforces permissions, times the call, converts
exceptions into typed errors, and emits a PII-safe log line.

Retries are limited: only a read-only tool with a retry policy retries, and only on a
transient database error. Business outcomes (not-found, ambiguity, forbidden, ownership)
are never retried.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.logging import get_logger
from app.tools.context import ToolContext
from app.tools.enums import ToolErrorCode
from app.tools.errors import ToolError, ToolFailure
from app.tools.models import ToolMetadata, ToolResult
from app.tools.registry import ToolDefinition, get_tool, is_reserved

logger = get_logger("agentops.tools")

_TRANSIENT_DB_ERRORS = (OperationalError,)


async def execute_tool(
    name: str, ctx: ToolContext, raw_input: BaseModel | dict[str, Any]
) -> ToolResult[BaseModel]:
    definition = get_tool(name)
    if definition is None:
        reason = (
            "Tool is reserved for a future stage and has no handler."
            if is_reserved(name)
            else "Unknown tool."
        )
        return _failure(name, ctx, ToolErrorCode.invalid_state, reason)

    start = time.perf_counter()
    error: ToolError | None = None
    data: BaseModel | None = None
    try:
        ctx.require_permission(definition.permission)
        params = definition.input_model.model_validate(raw_input)
        data = await _run_with_retry(definition, ctx, params)
    except ToolFailure as failure:
        error = failure.to_error()
    except ValidationError as exc:
        error = ToolError(
            code=ToolErrorCode.invalid_input,
            message=f"Invalid input: {exc.error_count()} validation error(s).",
        )
    except Exception:  # unexpected technical failure; never leak a stack trace
        logger.exception(
            "tool=%s correlation_id=%s unexpected failure", name, ctx.correlation_id
        )
        error = ToolError(
            code=ToolErrorCode.internal_error, message="An unexpected error occurred."
        )

    duration_ms = int((time.perf_counter() - start) * 1000)
    ok = error is None
    logger.info(
        "tool=%s correlation_id=%s ok=%s duration_ms=%s error_code=%s",
        name,
        ctx.correlation_id,
        ok,
        duration_ms,
        error.code.value if error else "none",
    )
    return ToolResult[BaseModel](
        ok=ok,
        tool=name,
        data=data,
        error=error,
        metadata=ToolMetadata(
            risk_level=definition.risk_level,
            tool_version=definition.version,
            duration_ms=duration_ms,
            correlation_id=ctx.correlation_id,
        ),
    )


async def _run_with_retry(
    definition: ToolDefinition, ctx: ToolContext, params: BaseModel
) -> BaseModel:
    if definition.handler is None:
        raise ToolFailure(
            ToolErrorCode.invalid_state, "Tool has no handler (reserved)."
        )
    attempts = definition.retry_policy.max_retries + 1
    for attempt in range(attempts):
        try:
            return await definition.handler(ctx, params)
        except _TRANSIENT_DB_ERRORS as exc:
            if attempt + 1 >= attempts:
                raise ToolFailure(
                    ToolErrorCode.dependency_unavailable,
                    "The database is temporarily unavailable.",
                    retryable=True,
                ) from exc
        except DBAPIError as exc:
            # Non-transient DB error: do not retry.
            raise ToolFailure(
                ToolErrorCode.dependency_unavailable, "A database error occurred."
            ) from exc
    # Unreachable: the loop always returns or raises.
    raise ToolFailure(ToolErrorCode.internal_error, "retry loop exhausted")


def _failure(
    name: str, ctx: ToolContext, code: ToolErrorCode, message: str
) -> ToolResult[BaseModel]:
    from app.rules.enums import RiskLevel

    return ToolResult[BaseModel](
        ok=False,
        tool=name,
        data=None,
        error=ToolError(code=code, message=message),
        metadata=ToolMetadata(
            risk_level=RiskLevel.read_only,
            tool_version="n/a",
            duration_ms=0,
            correlation_id=ctx.correlation_id,
        ),
    )
