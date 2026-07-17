"""Domain models and enumerations for Meridian & Co.

Importing this package pulls in every ORM model so that ``Base.metadata`` is fully
populated (used by Alembic and by test schema creation) and all relationships resolve.
"""

from __future__ import annotations

from app.models.customer import Customer
from app.models.enums import (
    CustomerTier,
    MessageSender,
    OrderStatus,
    PolicySourceType,
    PolicyStatus,
    ProductCategory,
    ShipmentStatus,
    TicketCategory,
    TicketPriority,
    TicketStatus,
    UserRole,
)
from app.models.model_call import ModelCall
from app.models.order import Order, OrderItem
from app.models.policy import Policy, PolicyChunk, PolicyVersion
from app.models.product import Product
from app.models.prompt_version import PromptVersion
from app.models.shipment import Shipment
from app.models.ticket import Ticket, TicketMessage
from app.models.user import User

__all__ = [
    "Customer",
    "CustomerTier",
    "MessageSender",
    "ModelCall",
    "Order",
    "OrderItem",
    "OrderStatus",
    "Policy",
    "PolicyChunk",
    "PolicySourceType",
    "PolicyStatus",
    "PolicyVersion",
    "Product",
    "ProductCategory",
    "PromptVersion",
    "Shipment",
    "ShipmentStatus",
    "Ticket",
    "TicketCategory",
    "TicketMessage",
    "TicketPriority",
    "TicketStatus",
    "User",
    "UserRole",
]
