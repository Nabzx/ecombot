"""Persist prompt versions and model calls with redaction already applied.

The service passes only redacted, safe payloads here. Prompt versions are upserted from
their canonical source (unique on name+version) so a call can reference the exact text
and hash used; existing rows are never mutated.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model_call import ModelCall
from app.models.prompt_version import PromptVersion
from app.prompts.models import PromptDefinition


async def ensure_prompt_version(
    session: AsyncSession, definition: PromptDefinition
) -> PromptVersion:
    """Return the stored prompt version, inserting it from source if absent.

    Immutable: an existing row is returned untouched, never overwritten.
    """
    existing = await session.scalar(
        select(PromptVersion).where(
            PromptVersion.name == definition.name,
            PromptVersion.semantic_version == definition.semantic_version,
        )
    )
    if existing is not None:
        return existing
    row = PromptVersion(
        name=definition.name,
        semantic_version=definition.semantic_version,
        task_type=definition.task_type,
        status=definition.status,
        template_hash=definition.template_hash,
        system_template=definition.system_template,
        user_template=definition.user_template,
        input_schema_name=definition.input_schema_name,
        output_schema_name=definition.output_schema_name,
        configuration_json={
            "allowed_tools": list(definition.allowed_tools),
            "max_input_length": definition.max_input_length,
            "max_output_tokens": definition.max_output_tokens,
            "default_temperature": definition.default_temperature,
        },
    )
    session.add(row)
    await session.flush()
    return row


async def record_model_call(
    session: AsyncSession,
    *,
    ticket_id: uuid.UUID | None,
    task_type: object,
    workflow_run_id: uuid.UUID | None = None,
    workflow_step_id: uuid.UUID | None = None,
    provider: str,
    model: str,
    prompt_version_id: uuid.UUID | None,
    correlation_id: str,
    status: object,
    input_token_count: int,
    output_token_count: int,
    token_source: str,
    estimated_cost_microunits: int,
    cost_status: str,
    latency_ms: int,
    finish_reason: str,
    repair_count: int,
    fallback_from_provider: str | None,
    fallback_reason: str | None,
    input_hash: str,
    output_hash: str | None,
    redacted_input_json: dict[str, object],
    parsed_output_json: dict[str, object] | None,
    raw_output_redacted: str | None,
    error_code: str | None,
    error_message: str | None,
    started_at: datetime,
    finished_at: datetime,
) -> ModelCall:
    """Insert one model-call audit row from an already-redacted payload."""
    row = ModelCall(
        ticket_id=ticket_id,
        workflow_run_id=workflow_run_id,
        workflow_step_id=workflow_step_id,
        task_type=task_type,
        provider=provider,
        model=model,
        prompt_version_id=prompt_version_id,
        correlation_id=correlation_id,
        status=status,
        input_token_count=input_token_count,
        output_token_count=output_token_count,
        total_token_count=input_token_count + output_token_count,
        token_source=token_source,
        estimated_cost_microunits=estimated_cost_microunits,
        cost_status=cost_status,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        repair_count=repair_count,
        fallback_from_provider=fallback_from_provider,
        fallback_reason=fallback_reason,
        input_hash=input_hash,
        output_hash=output_hash,
        redacted_input_json=redacted_input_json,
        parsed_output_json=parsed_output_json,
        raw_output_redacted=raw_output_redacted,
        error_code=error_code,
        error_message=error_message,
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add(row)
    await session.flush()
    return row
