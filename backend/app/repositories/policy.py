"""Policy repository."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.models.enums import PolicyStatus
from app.models.policy import Policy, PolicyVersion
from app.repositories.base import BaseRepository


class PolicyRepository(BaseRepository):
    async def list_policies(self) -> list[Policy]:
        stmt = select(Policy).order_by(Policy.topic, Policy.title)
        return list((await self.session.execute(stmt)).scalars())

    async def get_with_versions(self, policy_id: uuid.UUID) -> Policy | None:
        stmt = (
            select(Policy)
            .where(Policy.id == policy_id)
            .options(selectinload(Policy.versions))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_versions_by_topic(self, topic: str) -> list[PolicyVersion]:
        stmt = (
            select(PolicyVersion)
            .join(Policy)
            .where(Policy.topic == topic)
            .order_by(PolicyVersion.version)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get_active_version_for_date(
        self, topic: str, on_date: date
    ) -> PolicyVersion | None:
        """Return the active policy version effective on ``on_date`` for a topic.

        A version is usable when its status is ``active`` and ``on_date`` falls within
        ``[effective_from, effective_to)`` (an open-ended range if ``effective_to`` is
        null). The highest version wins if several qualify.
        """
        stmt = (
            select(PolicyVersion)
            .join(Policy)
            .where(
                Policy.topic == topic,
                PolicyVersion.status == PolicyStatus.active,
                PolicyVersion.effective_from <= on_date,
                or_(
                    PolicyVersion.effective_to.is_(None),
                    PolicyVersion.effective_to > on_date,
                ),
            )
            .order_by(PolicyVersion.version.desc())
        )
        return (await self.session.execute(stmt)).scalars().first()
