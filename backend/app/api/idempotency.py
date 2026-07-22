"""HTTP request-idempotency store for approval write endpoints (S6).

Scoped to (key, actor, operation). Same key + same request hash → replay the original
entity; same key + different request hash → conflict. Keys are bounded and non-empty.
This is *HTTP* idempotency, never the business idempotency guaranteeing exactly-once.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyRecord

MAX_KEY_LENGTH = 200


class IdempotencyOutcome(StrEnum):
    NEW = "new"
    REPLAY = "replay"
    CONFLICT = "conflict"


class IdempotencyKeyError(ValueError):
    """Raised for a missing/oversized idempotency key."""


@dataclass(frozen=True)
class IdempotencyLookup:
    outcome: IdempotencyOutcome
    response_entity_id: uuid.UUID | None = None


def request_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_key(key: str) -> str:
    key = key.strip()
    if not key:
        raise IdempotencyKeyError("idempotency key must not be empty")
    if len(key) > MAX_KEY_LENGTH:
        raise IdempotencyKeyError("idempotency key is too long")
    return key


class IdempotencyStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lookup(
        self, *, key: str, actor_id: uuid.UUID, operation: str, req_hash: str
    ) -> IdempotencyLookup:
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == key,
            IdempotencyRecord.actor_user_id == actor_id,
            IdempotencyRecord.operation == operation,
        )
        record = (await self._session.execute(stmt)).scalar_one_or_none()
        if record is None:
            return IdempotencyLookup(IdempotencyOutcome.NEW)
        if record.request_hash != req_hash:
            return IdempotencyLookup(IdempotencyOutcome.CONFLICT)
        return IdempotencyLookup(
            IdempotencyOutcome.REPLAY, response_entity_id=record.response_entity_id
        )

    async def store(
        self,
        *,
        key: str,
        actor_id: uuid.UUID,
        operation: str,
        req_hash: str,
        entity_id: uuid.UUID | None,
        now: datetime,
    ) -> None:
        self._session.add(
            IdempotencyRecord(
                idempotency_key=key,
                actor_user_id=actor_id,
                operation=operation,
                request_hash=req_hash,
                response_entity_id=entity_id,
                created_at=now,
            )
        )
        await self._session.flush()
