"""Immutable, hash-chained audit-event ORM (S7).

Every consequential/security-relevant event is an append-only row. Each row's
``entry_hash`` chains the previous row's hash, so deleting or altering any event breaks
the chain detectably. Metadata is PII-safe: identifiers, statuses and hashes only.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import UUIDPKMixin


class AuditEvent(UUIDPKMixin, Base):
    """One immutable audit-log entry, chained to its predecessor by hash."""

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("sequence", name="uq_audit_sequence"),
        UniqueConstraint("entry_hash", name="uq_audit_entry_hash"),
        Index("ix_audit_events_correlation", "correlation_id"),
        Index("ix_audit_events_type", "event_type"),
        Index("ix_audit_events_subject", "subject_type", "subject_id"),
    )

    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    actor_role: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<AuditEvent #{self.sequence} {self.event_type}>"
