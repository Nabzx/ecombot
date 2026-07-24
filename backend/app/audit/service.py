"""Audit service: record consequential events in the caller's transaction (S7).

Callers pass their own ``AsyncSession`` so the audit row commits *with* the event it
describes: there is never a consequential action without its audit record, and a
rollback drops both. The correlation id defaults to the observability context.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.enums import AuditEventType
from app.audit.repository import AuditRepository
from app.core.context import current
from app.models.audit import AuditEvent


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = AuditRepository(session)

    async def record(
        self,
        event_type: AuditEventType,
        *,
        occurred_at: datetime,
        subject_type: str,
        subject_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        actor_role: str = "system",
        summary: str = "",
        metadata: dict[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        return await self._repo.append(
            event_type=event_type.value,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            subject_type=subject_type,
            subject_id=subject_id,
            correlation_id=correlation_id or current().correlation_id,
            summary=summary,
            metadata=metadata or {},
            occurred_at=occurred_at,
        )
