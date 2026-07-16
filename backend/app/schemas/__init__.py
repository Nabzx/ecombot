"""Pydantic request/response schemas."""

from __future__ import annotations

from app.schemas.customer import CustomerDetail, CustomerSummary
from app.schemas.order import OrderDetail, OrderItemSchema, OrderSummary
from app.schemas.policy import PolicyDetail, PolicySummary, PolicyVersionDetail
from app.schemas.product import ProductSummary
from app.schemas.shipment import ShipmentDetail
from app.schemas.ticket import TicketDetail, TicketMessageSchema, TicketSummary
from app.schemas.user import UserSummary

__all__ = [
    "CustomerDetail",
    "CustomerSummary",
    "OrderDetail",
    "OrderItemSchema",
    "OrderSummary",
    "PolicyDetail",
    "PolicySummary",
    "PolicyVersionDetail",
    "ProductSummary",
    "ShipmentDetail",
    "TicketDetail",
    "TicketMessageSchema",
    "TicketSummary",
    "UserSummary",
]
