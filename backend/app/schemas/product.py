"""Product schemas."""

from __future__ import annotations

import uuid

from pydantic import computed_field

from app.models.enums import ProductCategory
from app.schemas.common import ORMModel, pence_to_gbp


class ProductSummary(ORMModel):
    id: uuid.UUID
    sku: str
    name: str
    category: ProductCategory
    unit_price_pence: int
    is_active: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unit_price_gbp(self) -> str:
        return pence_to_gbp(self.unit_price_pence)
