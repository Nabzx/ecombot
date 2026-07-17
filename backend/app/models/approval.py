"""Approval request and immutable approval-decision ORM (S6).

An approval request stores canonical, hashed snapshots of the deterministic rule result
and policy evidence so a decision (and later execution) can verify nothing was tampered
with. The decision table is append-only human/system provenance.
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
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.approvals.enums import ApprovalDecisionType, ApprovalStatus
from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import pg_enum


class ApprovalRequest(UUIDPKMixin, TimestampMixin, Base):
    """A Supervisor-approval request bound to one proposed action + hashed snapshots."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "requested_amount_pence IS NULL OR requested_amount_pence > 0",
            name="ck_approval_requested_amount_positive",
        ),
        CheckConstraint(
            "approved_amount_pence IS NULL OR maximum_allowed_amount_pence IS NULL "
            "OR approved_amount_pence <= maximum_allowed_amount_pence",
            name="ck_approval_amount_within_max",
        ),
        CheckConstraint(
            "expires_at > created_at", name="ck_approval_expiry_after_creation"
        ),
        # At most one open (pending) approval per proposed action.
        Index(
            "uq_approval_open_per_action",
            "proposed_action_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_approval_requests_status", "status"),
        Index("ix_approval_requests_run", "workflow_run_id"),
        Index("ix_approval_requests_ticket", "ticket_id"),
        Index("ix_approval_requests_requester", "requester_user_id"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    proposed_action_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposed_actions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    requester_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        pg_enum(ApprovalStatus, "approval_status"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(24), nullable=False)
    required_role: Mapped[str | None] = mapped_column(String(24), nullable=True)
    requested_amount_pence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    maximum_allowed_amount_pence: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    approved_amount_pence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    policy_citation_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    policy_version_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    rule_result_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rule_result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_snapshot_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False
    )
    evidence_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_response_subject: Mapped[str] = mapped_column(Text, nullable=False)
    draft_response_body: Mapped[str] = mapped_column(Text, nullable=False)
    request_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ApprovalRequest {self.action_type} {self.status}>"


class ApprovalDecision(UUIDPKMixin, Base):
    """An append-only record of one approval status change (human or system)."""

    __tablename__ = "approval_decisions"
    __table_args__ = (Index("ix_approval_decisions_request", "approval_request_id"),)

    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("approval_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision: Mapped[ApprovalDecisionType] = mapped_column(
        pg_enum(ApprovalDecisionType, "approval_decision_type"), nullable=False
    )
    # Null actor => a system-generated event (e.g. expiry sweep).
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_role: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_status: Mapped[str] = mapped_column(String(24), nullable=False)
    new_status: Mapped[str] = mapped_column(String(24), nullable=False)
    requested_amount_pence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_amount_pence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ApprovalDecision {self.decision} -> {self.new_status}>"
