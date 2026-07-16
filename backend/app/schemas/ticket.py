"""Ticket schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import (
    MessageSender,
    TicketCategory,
    TicketPriority,
    TicketStatus,
)
from app.schemas.common import ORMModel


class TicketMessageSchema(ORMModel):
    id: uuid.UUID
    sender: MessageSender
    body: str
    is_trusted: bool
    sequence_number: int
    created_at: datetime


class TicketSummary(ORMModel):
    id: uuid.UUID
    ticket_reference: str
    customer_id: uuid.UUID | None
    order_id: uuid.UUID | None
    category: TicketCategory
    status: TicketStatus
    subject: str
    priority: TicketPriority
    injection_flag: bool
    received_at: datetime
    seed_tag: str | None


class TicketDetail(TicketSummary):
    classification_confidence: float | None
    resolved_at: datetime | None
    messages: list[TicketMessageSchema]
