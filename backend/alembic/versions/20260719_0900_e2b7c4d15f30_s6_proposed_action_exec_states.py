"""S6: proposed-action execution statuses.

Adds ``approved_pending_execution``, ``completed`` and ``rejected`` to the
``proposed_action_status`` enum so an approved action can be tracked from approval through
execution. Purely additive: existing rows and values are untouched, and no prior migration
is edited.

Downgrade is a no-op: PostgreSQL cannot drop an enum value in place without recreating the
type (which would break existing rows), so the added values are left in place. Re-upgrade
is safe because ``ADD VALUE IF NOT EXISTS`` is idempotent.

Revision ID: e2b7c4d15f30
Revises: d1a6b3f92c40
Create Date: 2026-07-19 09:00:00
"""

from __future__ import annotations

from alembic import op

revision = "e2b7c4d15f30"
down_revision = "d1a6b3f92c40"
branch_labels = None
depends_on = None

_NEW_PROPOSED_ACTION_STATUSES = (
    "approved_pending_execution",
    "completed",
    "rejected",
)


def upgrade() -> None:
    for value in _NEW_PROPOSED_ACTION_STATUSES:
        op.execute(
            f"ALTER TYPE proposed_action_status ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    # Enum values cannot be removed in place without recreating the type; leaving the
    # additional values present is the practical, non-destructive downgrade.
    pass
