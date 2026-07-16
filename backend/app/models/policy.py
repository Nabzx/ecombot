"""Policy, PolicyVersion and PolicyChunk models.

S3 adds retrieval: each policy version carries source/trust metadata and index
provenance, and its text is split into immutable ``PolicyChunk`` rows that hold a
full-text ``search_vector`` (generated column) and a pgvector ``embedding``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import PolicySourceType, PolicyStatus, pg_enum

# Fixed embedding dimension for the active index (see docs/policy-indexing.md).
EMBEDDING_DIM = 256


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
    """A dated, versioned body of a policy plus its source-trust and index metadata."""

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
        Index("ix_policy_versions_source_type", "source_type"),
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

    # --- S3 source / trust metadata ---
    source_type: Mapped[PolicySourceType] = mapped_column(
        pg_enum(PolicySourceType, "policy_source_type"),
        nullable=False,
        server_default=PolicySourceType.official_policy.value,
    )
    source_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="en-GB"
    )
    jurisdiction: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="UK"
    )
    audience: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="support"
    )
    is_retrieval_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    # --- S3 index provenance ---
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    embedding_provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunker_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    index_schema_version: Mapped[str | None] = mapped_column(String(30), nullable=True)

    policy: Mapped[Policy] = relationship(back_populates="versions")
    chunks: Mapped[list[PolicyChunk]] = relationship(
        back_populates="policy_version",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PolicyChunk.chunk_index",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<PolicyVersion policy={self.policy_id} v{self.version} {self.status}>"


class PolicyChunk(UUIDPKMixin, TimestampMixin, Base):
    """An immutable retrieval chunk of a policy version.

    ``body`` is the exact source excerpt; ``search_text`` is the heading-contextualised
    text used for both full-text and embedding. ``search_vector`` is a Postgres
    generated ``tsvector``; ``embedding`` is the pgvector column (null until indexed).
    """

    __tablename__ = "policy_chunks"
    __table_args__ = (
        UniqueConstraint(
            "policy_version_id", "chunk_index", name="uq_policy_chunks_version_index"
        ),
        UniqueConstraint("citation_id", name="uq_policy_chunks_citation_id"),
        Index("ix_policy_chunks_version", "policy_version_id"),
        Index(
            "ix_policy_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_policy_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("policy_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str] = mapped_column(String(255), nullable=False)
    heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', search_text)", persisted=True),
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    citation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    policy_version: Mapped[PolicyVersion] = relationship(back_populates="chunks")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<PolicyChunk {self.citation_id}>"
