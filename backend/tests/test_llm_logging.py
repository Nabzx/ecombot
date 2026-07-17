"""Safe-logging tests: model-call logs never carry secrets, PII or prompt text."""

from __future__ import annotations

import logging

from app.llm.service import ModelService
from app.llm.tasks import builders


async def test_model_task_log_is_safe(caplog) -> None:  # type: ignore[no-untyped-def]
    service = ModelService()
    secret_message = (
        "email jane@example.com key sk-ABCDEF1234567890 order MER-2026-000001"
    )
    with caplog.at_level(logging.INFO, logger="app.llm.service"):
        await service.run_task(
            builders.build_identifier_request(message=secret_message)
        )

    records = [r for r in caplog.records if r.name == "app.llm.service"]
    assert records
    for record in records:
        blob = record.getMessage() + str(getattr(record, "__dict__", {}))
        assert "jane@example.com" not in blob
        assert "sk-ABCDEF1234567890" not in blob
        # The rendered prompt / raw customer message must not be logged.
        assert secret_message not in blob


async def test_log_contains_safe_metadata(caplog) -> None:  # type: ignore[no-untyped-def]
    service = ModelService()
    with caplog.at_level(logging.INFO, logger="app.llm.service"):
        await service.run_task(
            builders.build_classification_request(subject="s", message="refund please")
        )
    record = next(r for r in caplog.records if r.name == "app.llm.service")
    assert record.__dict__["task"] == "ticket_classification"
    assert record.__dict__["provider"] == "mock"
    assert "cost_status" in record.__dict__
