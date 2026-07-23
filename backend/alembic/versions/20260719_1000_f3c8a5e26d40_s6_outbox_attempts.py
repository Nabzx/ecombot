"""S6: immutable outbox attempt history.

Adds the append-only ``outbox_attempts`` table (one row per worker attempt on a job), so a
dead-lettered job explains every prior failure and a succeeded job has exactly one
successful attempt. Purely additive; no prior migration is edited.

Revision ID: f3c8a5e26d40
Revises: e2b7c4d15f30
Create Date: 2026-07-19 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3c8a5e26d40"
down_revision = "e2b7c4d15f30"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "outbox_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("outbox_job_id", sa.UUID(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=80), nullable=False),
        sa.Column("previous_status", sa.String(length=24), nullable=False),
        sa.Column("result_status", sa.String(length=24), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("lease_expires_at", _TS, nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("finished_at", _TS, nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["outbox_job_id"], ["outbox_jobs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "outbox_job_id", "attempt_number", name="uq_outbox_attempt_number"
        ),
        sa.CheckConstraint("attempt_number > 0", name="ck_outbox_attempt_positive"),
    )
    op.create_index(
        "ix_outbox_attempts_job", "outbox_attempts", ["outbox_job_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_attempts_job", table_name="outbox_attempts")
    op.drop_table("outbox_attempts")
