"""ModelCall ORM: an audited, redaction-safe record of a single model task run.

Stores only redacted input, parsed output, hashes and metadata — never API keys, raw
customer PII, hidden reasoning or provider stack traces. Cost is an exact integer in GBP
microunits. ``workflow_run_id`` is a nullable, FK-less column reserved for S5.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.llm.enums import ModelCallStatus, ModelTaskType
from app.models.enums import pg_enum


class ModelCall(UUIDPKMixin, TimestampMixin, Base):
    """One persisted model task execution (success, repaired or failed)."""

    __tablename__ = "model_calls"
    __table_args__ = (
        Index("ix_model_calls_task_type", "task_type"),
        Index("ix_model_calls_provider_model", "provider", "model"),
        Index("ix_model_calls_prompt_version", "prompt_version_id"),
        Index("ix_model_calls_ticket", "ticket_id"),
        Index("ix_model_calls_correlation", "correlation_id"),
        Index("ix_model_calls_status", "status"),
        Index("ix_model_calls_created_at", "created_at"),
        Index("ix_model_calls_workflow_run", "workflow_run_id"),
    )

    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Linked to the workflow engine in S5 (nullable: model tasks are also callable
    # standalone from the CLI/dev API without a workflow).
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_steps.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_type: Mapped[ModelTaskType] = mapped_column(
        pg_enum(ModelTaskType, "model_task_type"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ModelCallStatus] = mapped_column(
        pg_enum(ModelCallStatus, "model_call_status"), nullable=False
    )

    input_token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_source: Mapped[str] = mapped_column(String(24), nullable=False)
    estimated_cost_microunits: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    cost_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    cost_status: Mapped[str] = mapped_column(String(24), nullable=False)

    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finish_reason: Mapped[str] = mapped_column(String(24), nullable=False)
    repair_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_from_provider: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    fallback_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    redacted_input_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False
    )
    parsed_output_json: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    raw_output_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ModelCall {self.task_type} {self.provider} {self.status}>"
