"""Ticket and TicketMessage models.

No workflow logic lives here — S1 only stores tickets and their messages. The
``seed_tag`` column labels deterministic demo/adversarial fixtures so later security
and evaluation stages can find them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import (
    MessageSender,
    TicketCategory,
    TicketPriority,
    TicketStatus,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.order import Order


class Ticket(UUIDPKMixin, TimestampMixin, Base):
    """A support ticket. ``customer_id``/``order_id`` may be null until identified.

    Order-to-customer ownership consistency is enforced in the seed generator and the
    data-integrity check (it is a cross-row invariant, not a single-column constraint).
    """

    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(
            "classification_confidence IS NULL OR "
            "(classification_confidence >= 0 AND classification_confidence <= 1)",
            name="confidence_between_0_and_1",
        ),
        Index("ix_tickets_status", "status"),
        Index("ix_tickets_category", "category"),
        Index("ix_tickets_received_at", "received_at"),
        Index("ix_tickets_customer_id", "customer_id"),
        Index("ix_tickets_order_id", "order_id"),
        Index("ix_tickets_seed_tag", "seed_tag"),
    )

    ticket_reference: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=True,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=True,
    )
    category: Mapped[TicketCategory] = mapped_column(
        pg_enum(TicketCategory, "ticket_category"), nullable=False
    )
    status: Mapped[TicketStatus] = mapped_column(
        pg_enum(TicketStatus, "ticket_status"),
        nullable=False,
        default=TicketStatus.received,
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    classification_confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    injection_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    priority: Mapped[TicketPriority] = mapped_column(
        pg_enum(TicketPriority, "ticket_priority"),
        nullable=False,
        default=TicketPriority.normal,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Deterministic fixture label (e.g. DEMO-REFUND-APPROVAL-001, ADV-INJECTION-003).
    seed_tag: Mapped[str | None] = mapped_column(String(60), nullable=True)

    customer: Mapped[Customer | None] = relationship(back_populates="tickets")
    order: Mapped[Order | None] = relationship(back_populates="tickets")
    messages: Mapped[list[TicketMessage]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TicketMessage.sequence_number",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Ticket {self.ticket_reference} category={self.category}>"


class TicketMessage(UUIDPKMixin, Base):
    """A single message on a ticket. Immutable, so it has only ``created_at``.

    Customer-provided messages are untrusted input and default to ``is_trusted=False``;
    adversarial content is stored verbatim for later prompt-injection evaluation.
    """

    __tablename__ = "ticket_messages"
    __table_args__ = (
        CheckConstraint("length(btrim(body)) > 0", name="body_not_empty"),
        CheckConstraint("sequence_number >= 1", name="sequence_number_positive"),
        UniqueConstraint(
            "ticket_id", "sequence_number", name="uq_ticket_messages_ticket_sequence"
        ),
        Index("ix_ticket_messages_ticket_id", "ticket_id"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender: Mapped[MessageSender] = mapped_column(
        pg_enum(MessageSender, "message_sender"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sequence_number: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ticket: Mapped[Ticket] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<TicketMessage ticket={self.ticket_id} seq={self.sequence_number}>"
