"""Executed-action and refund-ledger ORM (S6).

Each successful consequential effect has exactly one immutable ``executed_actions`` row,
guaranteed by unique idempotency keys and a single execution transaction. The refund
ledger is the authoritative record the S2 refund-history port now reads.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.actions.enums import ExecutedActionStatus, RefundEntryType
from app.db.base import Base
from app.db.mixins import UUIDPKMixin
from app.models.enums import pg_enum


class ExecutedAction(UUIDPKMixin, Base):
    """The single immutable record of one executed (simulated) consequential action."""

    __tablename__ = "executed_actions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_executed_actions_idempotency"),
        UniqueConstraint("outbox_job_id", name="uq_executed_actions_outbox"),
        CheckConstraint(
            "amount_pence IS NULL OR amount_pence > 0",
            name="ck_executed_amount_positive",
        ),
        Index("ix_executed_actions_order", "order_id"),
        Index("ix_executed_actions_approval", "approval_request_id"),
    )

    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    outbox_job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("outbox_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[ExecutedActionStatus] = mapped_column(
        pg_enum(ExecutedActionStatus, "executed_action_status"), nullable=False
    )
    amount_pence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    business_effect_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    precondition_snapshot_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False
    )
    precondition_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    executed_by: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ExecutedAction {self.action_type} {self.status}>"


class RefundLedgerEntry(UUIDPKMixin, Base):
    """An immutable refund ledger line — the authoritative prior-refund source."""

    __tablename__ = "refund_ledger_entries"
    __table_args__ = (
        UniqueConstraint("executed_action_id", name="uq_ledger_executed_action"),
        UniqueConstraint("idempotency_key", name="uq_ledger_idempotency"),
        CheckConstraint("amount_pence > 0", name="ck_ledger_amount_positive"),
        Index("ix_refund_ledger_order", "order_id"),
    )

    executed_action_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("executed_actions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("order_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    amount_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    entry_type: Mapped[RefundEntryType] = mapped_column(
        pg_enum(RefundEntryType, "refund_entry_type"), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<RefundLedgerEntry order={self.order_id} {self.amount_pence}p>"
