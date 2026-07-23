"""Immutable outbox attempt-history ORM (S6).

One append-only row per worker attempt on a job. The aggregate counters live on
``outbox_jobs``; this table is the durable, ordered record of *every* attempt — so a
dead-lettered job explains each prior failure, and a succeeded job has exactly one
successful attempt.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import UUIDPKMixin


class OutboxAttempt(UUIDPKMixin, Base):
    """A single immutable record of one worker attempt on an outbox job."""

    __tablename__ = "outbox_attempts"
    __table_args__ = (
        UniqueConstraint(
            "outbox_job_id", "attempt_number", name="uq_outbox_attempt_number"
        ),
        CheckConstraint("attempt_number > 0", name="ck_outbox_attempt_positive"),
        Index("ix_outbox_attempts_job", "outbox_job_id"),
    )

    outbox_job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("outbox_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(80), nullable=False)
    previous_status: Mapped[str] = mapped_column(String(24), nullable=False)
    result_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<OutboxAttempt job={self.outbox_job_id} "
            f"n={self.attempt_number} {self.result_status}>"
        )
