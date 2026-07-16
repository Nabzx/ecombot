"""Typed execution context passed to every tool.

Contains only what deterministic tools need: a DB session, an injected clock, the
permission set, an optional customer scope and ticket id, an actor label, a correlation
id and a timeout. No LLM-specific state; no global mutable state.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.rules.clock import Clock, SystemClock
from app.tools.enums import Permission, ToolErrorCode
from app.tools.errors import ToolFailure, forbidden


@dataclass(slots=True)
class ToolContext:
    permissions: frozenset[Permission]
    clock: Clock = field(default_factory=SystemClock)
    session: AsyncSession | None = None
    actor: str = "system"
    customer_scope: uuid.UUID | None = None
    ticket_id: uuid.UUID | None = None
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timeout_ms: int = 5_000

    def has_permission(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require_permission(self, permission: Permission) -> None:
        if permission not in self.permissions:
            raise forbidden(f"Missing required permission: {permission.value}")

    def require_session(self) -> AsyncSession:
        if self.session is None:
            raise ToolFailure(
                ToolErrorCode.dependency_unavailable,
                "This tool requires a database session.",
            )
        return self.session
