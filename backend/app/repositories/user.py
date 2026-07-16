"""User repository.

``list`` returns password-free ``UserSummary`` schemas so that listing can never leak
a hash. ``get_by_email`` returns the ORM model because a later auth stage needs the
hash to verify a password.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.user import User
from app.repositories.base import DEFAULT_LIMIT, BaseRepository, Page, clamp_limit
from app.schemas.user import UserSummary


class UserRepository(BaseRepository):
    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.strip().lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list(
        self, *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Page[UserSummary]:
        limit = clamp_limit(limit)
        total = (
            await self.session.execute(select(func.count()).select_from(User))
        ).scalar_one()
        rows = (
            await self.session.execute(
                select(User)
                .order_by(User.created_at, User.id)
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        items = [UserSummary.model_validate(user) for user in rows]
        return Page(items=items, total=total, limit=limit, offset=offset)
