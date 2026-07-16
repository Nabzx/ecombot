"""Shipment repository."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.shipment import Shipment
from app.repositories.base import BaseRepository


class ShipmentRepository(BaseRepository):
    async def get(self, shipment_id: uuid.UUID) -> Shipment | None:
        return await self.session.get(Shipment, shipment_id)

    async def get_by_order(self, order_id: uuid.UUID) -> Shipment | None:
        stmt = select(Shipment).where(Shipment.order_id == order_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_tracking(self, tracking_number: str) -> Shipment | None:
        stmt = select(Shipment).where(
            Shipment.tracking_number == tracking_number.strip()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
