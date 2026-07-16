"""Shipment schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from app.models.enums import ShipmentStatus
from app.schemas.common import ORMModel


class ShipmentDetail(ORMModel):
    id: uuid.UUID
    order_id: uuid.UUID
    carrier: str
    tracking_number: str
    status: ShipmentStatus
    promised_delivery_date: date
    shipped_at: datetime | None
    delivered_at: datetime | None
    last_updated_at: datetime | None
