"""Development-only, read-only model-layer inspection endpoints.

Mounted only when ``ENVIRONMENT`` is development or test. No writes, no tool execution,
no arbitrary provider base URLs from request data, no API keys returned, no unredacted
prompt inspection. Requests are size-limited and use the model service rather than
duplicating logic.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.llm.providers.factory import build_providers
from app.llm.service import ModelService
from app.llm.tasks import builders
from app.llm.tasks.definitions import TASK_DEFINITIONS
from app.models.enums import TicketCategory
from app.models.model_call import ModelCall
from app.prompts.registry import get_prompt_registry

router = APIRouter(prefix="/api/dev/models", tags=["dev-models"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

_MAX_TEXT = 4000


class ClassifyRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=_MAX_TEXT)
    injection_flag: bool = False


class ExtractRequest(BaseModel):
    message: str = Field(min_length=1, max_length=_MAX_TEXT)


class DraftRequest(BaseModel):
    customer_name: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=60)
    message: str = Field(min_length=1, max_length=_MAX_TEXT)
    rule_result: str = Field(min_length=1, max_length=600)
    allowed_actions: list[str] = Field(min_length=1, max_length=10)
    approval_required: bool = False
    requires_more_information: bool = False
    citations: list[str] = Field(default_factory=list, max_length=20)


@router.get("/providers")
async def list_providers(settings: SettingsDep) -> dict[str, object]:
    providers = build_providers(settings)
    return {
        "default_provider": settings.llm_default_provider,
        "fallback_order": settings.llm_fallback_order,
        "providers": [
            {
                "name": name,
                "available": provider.is_available(),
                "default_model": provider.default_model,
                "capabilities": sorted(
                    c.value for c in provider.capabilities.capabilities
                ),
            }
            for name, provider in sorted(providers.items())
        ],
    }


@router.get("/tasks")
async def list_tasks() -> dict[str, object]:
    return {
        "tasks": [
            {
                "task_type": t.value,
                "purpose": d.purpose,
                "prompt": d.prompt_name,
                "output_schema": d.output_schema_name,
                "allowed_tools": list(d.allowed_tools),
                "max_input_chars": d.max_input_chars,
                "max_output_tokens": d.max_output_tokens,
            }
            for t, d in TASK_DEFINITIONS.items()
        ]
    }


def _result_payload(result: object) -> dict[str, object]:
    from app.llm.service import ModelTaskResult

    assert isinstance(result, ModelTaskResult)  # noqa: S101 - narrow type
    return {
        "success": result.success,
        "task": result.task_type.value,
        "provider": result.provider,
        "requested_provider": result.requested_provider,
        "fallback_from": result.fallback_from,
        "prompt": f"{result.prompt_name}@{result.prompt_version}",
        "repair_count": result.repair_count,
        "cost_status": result.cost.status.value,
        "warnings": result.warnings,
        "error": result.error.code.value if result.error else None,
        "output": result.output.model_dump(mode="json") if result.output else None,
    }


@router.post("/classify")
async def classify(body: ClassifyRequest) -> dict[str, object]:
    request = builders.build_classification_request(
        subject=body.subject,
        message=body.message,
        injection_flag=body.injection_flag,
    )
    result = await ModelService().run_task(request)
    return _result_payload(result)


@router.post("/extract")
async def extract(body: ExtractRequest) -> dict[str, object]:
    request = builders.build_identifier_request(message=body.message)
    result = await ModelService().run_task(request)
    return _result_payload(result)


@router.post("/draft")
async def draft(body: DraftRequest) -> dict[str, object]:
    valid_categories = {c.value for c in TicketCategory}
    if body.category not in valid_categories:
        raise HTTPException(status_code=422, detail="unknown category")
    request = builders.build_response_drafting_request(
        customer_name=body.customer_name,
        category=body.category,
        message=body.message,
        rule_result=body.rule_result,
        allowed_actions=body.allowed_actions,
        approval_required=body.approval_required,
        requires_more_information=body.requires_more_information,
        citations=body.citations,
    )
    result = await ModelService().run_task(request)
    return _result_payload(result)


@router.get("/model-calls")
async def list_model_calls(session: SessionDep) -> dict[str, object]:
    total = await session.scalar(select(func.count()).select_from(ModelCall))
    rows = await session.execute(
        select(
            ModelCall.task_type,
            ModelCall.provider,
            ModelCall.status,
            ModelCall.repair_count,
            ModelCall.created_at,
        )
        .order_by(ModelCall.created_at.desc())
        .limit(50)
    )
    return {
        "total": total,
        "recent": [
            {
                "task": task.value,
                "provider": provider,
                "status": status.value,
                "repair_count": repair,
                "created_at": created.isoformat(),
            }
            for task, provider, status, repair, created in rows
        ],
    }


@router.get("/prompts")
async def list_prompts() -> dict[str, object]:
    return {
        "prompts": [
            {
                "name": d.name,
                "version": d.semantic_version,
                "status": d.status.value,
                "task": d.task_type.value,
                "hash": d.template_hash,
            }
            for d in get_prompt_registry().all_definitions()
        ]
    }
