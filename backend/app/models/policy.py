"""Policy and PolicyVersion models.

No embeddings here — pgvector columns belong to the RAG stage (S3). S1 stores policy
text and versioned effective-date ranges only.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import PolicyStatus, pg_enum


class Policy(UUIDPKMixin, TimestampMixin, Base):
    """A company policy topic (e.g. returns, refunds). Text lives in its versions."""

    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint("topic", "title", name="uq_policies_topic_title"),
        Index("ix_policies_topic", "topic"),
    )

    topic: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    versions: Mapped[list[PolicyVersion]] = relationship(
        back_populates="policy",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PolicyVersion.version",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Policy {self.topic}/{self.title!r}>"


class PolicyVersion(UUIDPKMixin, TimestampMixin, Base):
    """A dated, versioned body of a policy. Only one is normally active per period."""

    __tablename__ = "policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "policy_id", "version", name="uq_policy_versions_policy_version"
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="effective_range_valid",
        ),
        Index("ix_policy_versions_status", "status"),
        Index("ix_policy_versions_effective_from", "effective_from"),
    )

    policy_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("policies.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PolicyStatus] = mapped_column(
        pg_enum(PolicyStatus, "policy_status"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    policy: Mapped[Policy] = relationship(back_populates="versions")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<PolicyVersion policy={self.policy_id} v{self.version} {self.status}>"
