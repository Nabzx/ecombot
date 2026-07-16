"""enable pgvector extension

Revision ID: 0001_enable_pgvector
Revises:
Create Date: 2026-07-16

Enables the ``vector`` extension so later stages can add embedding columns. No domain
tables are created in S0; this migration exists to make the extension part of the
version-controlled, reproducible schema history rather than ad-hoc setup.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_enable_pgvector"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
