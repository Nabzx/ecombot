"""Async, typed data-access repositories."""

from __future__ import annotations

from app.repositories.base import Page
from app.repositories.customer import CustomerRepository
from app.repositories.order import OrderRepository
from app.repositories.policy import PolicyRepository
from app.repositories.product import ProductRepository
from app.repositories.shipment import ShipmentRepository
from app.repositories.ticket import TicketRepository
from app.repositories.user import UserRepository

__all__ = [
    "CustomerRepository",
    "OrderRepository",
    "Page",
    "PolicyRepository",
    "ProductRepository",
    "ShipmentRepository",
    "TicketRepository",
    "UserRepository",
]
