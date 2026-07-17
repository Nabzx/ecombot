"""PromptVersion ORM: the immutable record of a prompt version actually used.

Mirrors the canonical YAML source (``app/prompts/templates``) into the database so a
model call can reference exactly which prompt text and hash produced it. Rows are
immutable once referenced by a model call.
"""

from __future__ import annotations

from sqlalchemy import Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.llm.enums import ModelTaskType, PromptStatus
from app.models.enums import pg_enum


class PromptVersion(UUIDPKMixin, TimestampMixin, Base):
    """One immutable, hashable prompt version."""

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint(
            "name", "semantic_version", name="uq_prompt_versions_name_version"
        ),
        Index("ix_prompt_versions_task_status", "task_type", "status"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(20), nullable=False)
    task_type: Mapped[ModelTaskType] = mapped_column(
        pg_enum(ModelTaskType, "model_task_type"), nullable=False
    )
    status: Mapped[PromptStatus] = mapped_column(
        pg_enum(PromptStatus, "prompt_status"), nullable=False
    )
    template_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    system_template: Mapped[str] = mapped_column(Text, nullable=False)
    user_template: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    output_schema_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    configuration_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<PromptVersion {self.name}@{self.semantic_version}>"
