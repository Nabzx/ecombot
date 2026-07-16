"""Support-team user model (Support Agents and Supervisors)."""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import UserRole, pg_enum


class User(UUIDPKMixin, TimestampMixin, Base):
    """A synthetic support employee who will later authenticate into AgentOps.

    ``hashed_password`` is intentionally never returned by repository listing methods
    or exposed by any Pydantic schema.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"), nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<User {self.email} role={self.role}>"
