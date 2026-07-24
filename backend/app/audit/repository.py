"""Audit-log repository: append-only writes and chain verification (S7).

Appends serialise on a transaction-scoped advisory lock so the sequence and the
hash chain stay consistent even under concurrent writers, without a table-wide lock.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.hashing import GENESIS_HASH, compute_entry_hash
from app.models.audit import AuditEvent

# A stable key for the advisory lock that serialises audit appends.
_AUDIT_LOCK_KEY = 748_2026


@dataclass
class ChainVerification:
    ok: bool
    checked: int
    broken_sequence: int | None = None
    reason: str | None = None


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        event_type: str,
        actor_user_id: uuid.UUID | None,
        actor_role: str,
        subject_type: str,
        subject_id: uuid.UUID | None,
        correlation_id: str,
        summary: str,
        metadata: dict[str, object],
        occurred_at: datetime,
    ) -> AuditEvent:
        # Serialise appends within this transaction so sequence + chain stay consistent.
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY}
        )
        tail = (
            await self._session.execute(
                select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
            )
        ).scalar_one_or_none()
        sequence = (tail.sequence + 1) if tail is not None else 1
        previous_hash = tail.entry_hash if tail is not None else GENESIS_HASH

        fields = {
            "sequence": sequence,
            "event_type": event_type,
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "actor_role": actor_role,
            "subject_type": subject_type,
            "subject_id": str(subject_id) if subject_id else None,
            "correlation_id": correlation_id,
            "summary": summary,
            "metadata_json": metadata,
            "previous_hash": previous_hash,
            "occurred_at": occurred_at.isoformat(),
        }
        entry = AuditEvent(
            sequence=sequence,
            event_type=event_type,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            subject_type=subject_type,
            subject_id=subject_id,
            correlation_id=correlation_id,
            summary=summary,
            metadata_json=metadata,
            previous_hash=previous_hash,
            entry_hash=compute_entry_hash(fields),
            occurred_at=occurred_at,
            created_at=occurred_at,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get(self, event_id: uuid.UUID) -> AuditEvent | None:
        return await self._session.get(AuditEvent, event_id)

    async def count(self) -> int:
        return int(
            (
                await self._session.execute(select(func.count(AuditEvent.id)))
            ).scalar_one()
        )

    async def list_events(
        self,
        *,
        event_type: str | None = None,
        correlation_id: str | None = None,
        subject_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        stmt: Select[tuple[AuditEvent]] = select(AuditEvent)
        if event_type is not None:
            stmt = stmt.where(AuditEvent.event_type == event_type)
        if correlation_id is not None:
            stmt = stmt.where(AuditEvent.correlation_id == correlation_id)
        if subject_id is not None:
            stmt = stmt.where(AuditEvent.subject_id == subject_id)
        stmt = stmt.order_by(AuditEvent.sequence.desc()).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars())

    async def list_for_correlation(self, correlation_id: str) -> list[AuditEvent]:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.correlation_id == correlation_id)
            .order_by(AuditEvent.sequence.asc())
        )
        return list((await self._session.execute(stmt)).scalars())

    async def verify_chain(self) -> ChainVerification:
        """Walk the whole chain in order, recomputing each hash."""
        rows = list(
            (
                await self._session.execute(
                    select(AuditEvent).order_by(AuditEvent.sequence.asc())
                )
            ).scalars()
        )
        previous = GENESIS_HASH
        expected_seq = 1
        for row in rows:
            if row.sequence != expected_seq:
                return ChainVerification(
                    False, expected_seq - 1, row.sequence, "sequence gap"
                )
            if row.previous_hash != previous:
                return ChainVerification(
                    False, expected_seq - 1, row.sequence, "previous-hash mismatch"
                )
            fields = {
                "sequence": row.sequence,
                "event_type": row.event_type,
                "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
                "actor_role": row.actor_role,
                "subject_type": row.subject_type,
                "subject_id": str(row.subject_id) if row.subject_id else None,
                "correlation_id": row.correlation_id,
                "summary": row.summary,
                "metadata_json": row.metadata_json,
                "previous_hash": row.previous_hash,
                "occurred_at": row.occurred_at.isoformat(),
            }
            if compute_entry_hash(fields) != row.entry_hash:
                return ChainVerification(
                    False, expected_seq - 1, row.sequence, "entry-hash mismatch"
                )
            previous = row.entry_hash
            expected_seq += 1
        return ChainVerification(True, len(rows))
