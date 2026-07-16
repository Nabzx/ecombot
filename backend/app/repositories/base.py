"""Repository base class and pagination result type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(slots=True)
class Page(Generic[T]):
    """A page of results plus the total count for the underlying query."""

    items: list[T]
    total: int
    limit: int
    offset: int


def clamp_limit(limit: int) -> int:
    """Keep page sizes within sane bounds."""
    return max(1, min(limit, MAX_LIMIT))


class BaseRepository:
    """Holds the async session shared by all repository methods."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
