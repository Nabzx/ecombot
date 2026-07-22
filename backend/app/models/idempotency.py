"""HTTP request-idempotency records (S6).

Distinct from *business-action* idempotency (which lives on approvals/outbox/executed
actions). This maps an ``Idempotency-Key`` header, scoped to actor + operation, to the
original response entity so a retried identical request returns the same result and a
reused key with a different payload is a conflict.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import UUIDPKMixin


class IdempotencyRecord(UUIDPKMixin, Base):
    """One stored idempotent request outcome, scoped to (key, actor, operation)."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            "actor_user_id",
            "operation",
            name="uq_idempotency_key_actor_operation",
        ),
    )

    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<IdempotencyRecord {self.operation} {self.idempotency_key[:12]}>"
