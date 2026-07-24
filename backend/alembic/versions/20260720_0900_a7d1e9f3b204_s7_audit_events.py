"""S7: immutable hash-chained audit-event log.

Adds the append-only ``audit_events`` table. Purely additive; no prior migration edited.

Revision ID: a7d1e9f3b204
Revises: f3c8a5e26d40
Create Date: 2026-07-20 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7d1e9f3b204"
down_revision = "f3c8a5e26d40"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)
_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_role", sa.String(length=24), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metadata_json", _JSONB, nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", _TS, nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequence", name="uq_audit_sequence"),
        sa.UniqueConstraint("entry_hash", name="uq_audit_entry_hash"),
    )
    op.create_index(
        "ix_audit_events_correlation", "audit_events", ["correlation_id"], unique=False
    )
    op.create_index(
        "ix_audit_events_type", "audit_events", ["event_type"], unique=False
    )
    op.create_index(
        "ix_audit_events_subject",
        "audit_events",
        ["subject_type", "subject_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_subject", table_name="audit_events")
    op.drop_index("ix_audit_events_type", table_name="audit_events")
    op.drop_index("ix_audit_events_correlation", table_name="audit_events")
    op.drop_table("audit_events")
