"""PostgreSQL-backed persistence tests for prompt versions and model calls."""

from __future__ import annotations

import pytest
from app.llm.enums import ModelTaskType
from app.llm.persistence import ensure_prompt_version
from app.llm.service import ModelService
from app.llm.tasks import builders
from app.models.model_call import ModelCall
from app.models.prompt_version import PromptVersion
from app.prompts.registry import get_prompt_registry
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.usefixtures("_prepare_test_database")


async def test_prompt_version_saved_and_immutable(db_session: AsyncSession) -> None:
    definition = get_prompt_registry().active_for_task(
        ModelTaskType.TICKET_CLASSIFICATION
    )
    row = await ensure_prompt_version(db_session, definition)
    assert row.template_hash == definition.template_hash
    # Second call returns the same row (immutable, not duplicated).
    again = await ensure_prompt_version(db_session, definition)
    assert again.id == row.id
    count = await db_session.scalar(select(func.count()).select_from(PromptVersion))
    assert count == 1


async def test_prompt_version_uniqueness(db_session: AsyncSession) -> None:
    definition = get_prompt_registry().active_for_task(ModelTaskType.EVIDENCE_SUMMARY)
    await ensure_prompt_version(db_session, definition)
    db_session.add(
        PromptVersion(
            name=definition.name,
            semantic_version=definition.semantic_version,
            task_type=definition.task_type,
            status=definition.status,
            template_hash="x",
            system_template="s",
            user_template="u",
            input_schema_name="i",
            output_schema_name=None,
            configuration_json={},
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_model_call_persisted_with_redaction(db_session: AsyncSession) -> None:
    service = ModelService()
    request = builders.build_identifier_request(
        message="email jane@example.com order MER-2026-000123 phone 07911123456"
    )
    result = await service.run_task(request, session=db_session)
    assert result.success
    await db_session.flush()

    row = await db_session.scalar(
        select(ModelCall).order_by(ModelCall.created_at.desc()).limit(1)
    )
    assert row is not None
    assert row.provider == "mock"
    assert row.prompt_version_id is not None
    assert row.output_hash is not None
    assert row.estimated_cost_microunits == 0
    # Redaction applied; the MER order id is preserved but PII is not.
    serialized = str(row.redacted_input_json)
    assert "jane@example.com" not in serialized
    assert "07911123456" not in serialized
    assert "MER-2026-000123" in serialized


async def test_repaired_call_records_repair_count(db_session: AsyncSession) -> None:
    service = ModelService()
    request = builders.build_classification_request(
        subject="x",
        message="refund please",
        allowed_categories=["refund_request"],
    )
    request.mock_scenario = "repair_ok"
    result = await service.run_task(request, session=db_session)
    await db_session.flush()
    assert result.repair_count == 1
    row = await db_session.scalar(
        select(ModelCall).order_by(ModelCall.created_at.desc()).limit(1)
    )
    assert row is not None
    assert row.repair_count == 1
    assert row.status.value == "repaired"


async def test_no_secret_leaks_into_persistence(db_session: AsyncSession) -> None:
    service = ModelService()
    request = builders.build_identifier_request(
        message="my key is sk-ABCDEF1234567890 and card 4111 1111 1111 1111"
    )
    await service.run_task(request, session=db_session)
    await db_session.flush()
    row = await db_session.scalar(
        select(ModelCall).order_by(ModelCall.created_at.desc()).limit(1)
    )
    assert row is not None
    blob = str(row.redacted_input_json)
    assert "sk-ABCDEF1234567890" not in blob
    assert "4111 1111 1111 1111" not in blob
