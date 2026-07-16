"""Shared schema base and money formatting helpers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Base for schemas populated from SQLAlchemy ORM instances."""

    model_config = ConfigDict(from_attributes=True)


def pence_to_gbp(pence: int) -> str:
    """Format integer pennies as a GBP string, e.g. ``1234`` -> ``£12.34``."""
    sign = "-" if pence < 0 else ""
    value = abs(pence)
    return f"{sign}£{value // 100}.{value % 100:02d}"
