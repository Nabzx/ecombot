"""Domain models and enumerations for Meridian & Co.

Importing this package pulls in every ORM model so that ``Base.metadata`` is fully
populated (used by Alembic and by test schema creation).
"""

from __future__ import annotations

from app.models.enums import (
    CustomerTier,
    MessageSender,
    OrderStatus,
    PolicyStatus,
    ShipmentStatus,
    TicketCategory,
    TicketPriority,
    TicketStatus,
    UserRole,
)

__all__ = [
    "CustomerTier",
    "MessageSender",
    "OrderStatus",
    "PolicyStatus",
    "ShipmentStatus",
    "TicketCategory",
    "TicketPriority",
    "TicketStatus",
    "UserRole",
]
