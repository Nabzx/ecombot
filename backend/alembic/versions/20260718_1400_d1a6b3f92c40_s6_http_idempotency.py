"""s6 http idempotency

Revision ID: d1a6b3f92c40
Revises: c9e5f1a83b20
Create Date: 2026-07-18 14:00:00.000000

Adds ``idempotency_records`` so the approval write APIs can honour an ``Idempotency-Key``
header (same key + actor + operation + payload returns the original result; a reused key with
a different payload is a conflict). Separate from business-action idempotency, which lives on
approvals/outbox/executed actions. Existing data is untouched; downgrade drops the table.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1a6b3f92c40"
down_revision: str | None = "c9e5f1a83b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
        sa.Column("operation", sa.String(length=60), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_entity_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            "actor_user_id",
            "operation",
            name="uq_idempotency_key_actor_operation",
        ),
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
