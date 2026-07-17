"""Typed auth models: token payloads, token pairs and the authenticated actor."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from app.auth.enums import Permission, TokenType, permissions_for
from app.models.enums import UserRole


class TokenPayload(BaseModel):
    """Decoded, validated JWT claims."""

    model_config = ConfigDict(extra="ignore")

    sub: str  # user id
    role: UserRole
    token_type: TokenType
    iat: int
    exp: int
    jti: str


class TokenPair(BaseModel):
    """An access token (and optional refresh token) plus metadata."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"  # noqa: S105 - OAuth token_type label, not a secret
    expires_in: int


@dataclass(frozen=True)
class AuthenticatedUser:
    """The actor resolved from a validated access token — the ONLY source of identity.

    Request bodies never supply the actor; a forged user id in JSON is ignored.
    """

    user_id: uuid.UUID
    role: UserRole
    email: str
    is_active: bool
    permissions: frozenset[Permission] = field(default_factory=frozenset)

    @classmethod
    def build(
        cls,
        *,
        user_id: uuid.UUID,
        role: UserRole,
        email: str,
        is_active: bool,
    ) -> AuthenticatedUser:
        return cls(
            user_id=user_id,
            role=role,
            email=email,
            is_active=is_active,
            permissions=permissions_for(role),
        )

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    @property
    def is_supervisor(self) -> bool:
        return self.role == UserRole.supervisor
