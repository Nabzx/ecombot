"""Order schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import computed_field

from app.models.enums import OrderStatus
from app.schemas.common import ORMModel, pence_to_gbp
from app.schemas.shipment import ShipmentDetail


class OrderItemSchema(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    quantity: int
    unit_price_pence: int
    line_total_pence: int
    is_returned: bool
    returned_at: datetime | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def line_total_gbp(self) -> str:
        return pence_to_gbp(self.line_total_pence)


class OrderSummary(ORMModel):
    id: uuid.UUID
    order_number: str
    customer_id: uuid.UUID
    status: OrderStatus
    total_paid_pence: int
    placed_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_paid_gbp(self) -> str:
        return pence_to_gbp(self.total_paid_pence)


class OrderDetail(OrderSummary):
    subtotal_pence: int
    delivery_fee_pence: int
    discount_pence: int
    items: list[OrderItemSchema]
    shipment: ShipmentDetail | None = None
