"""s6 approvals and execution

Revision ID: c9e5f1a83b20
Revises: b7d2e9c04a10
Create Date: 2026-07-18 09:00:00.000000

Adds the human-approval layer, durable outbox, executed-action records and refund ledger,
plus the S6 workflow-state enum values for support-ticket-v2. Existing S0-S5 data and v1
workflow runs are untouched.

Downgrade drops the new tables and enum types. It cannot remove the workflow_state enum
values added here (PostgreSQL does not support DROP VALUE); those are harmless and unused by
v1 runs, and are documented as a known downgrade limitation.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9e5f1a83b20"
down_revision: str | None = "b7d2e9c04a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_WORKFLOW_STATES = (
    "approved_pending_execution",
    "executing_action",
    "approval_expired",
    "action_failed",
    "manual_action_required",
    "approval_rejected",
    "action_succeeded",
)

APPROVAL_STATUS = postgresql.ENUM(
    "pending",
    "approved",
    "rejected",
    "expired",
    "cancelled",
    "superseded",
    "execution_pending",
    "executed",
    "execution_failed",
    name="approval_status",
    create_type=False,
)
APPROVAL_DECISION_TYPE = postgresql.ENUM(
    "approve",
    "reject",
    "cancel",
    "expire",
    "retry_authorised",
    name="approval_decision_type",
    create_type=False,
)
OUTBOX_STATUS = postgresql.ENUM(
    "pending",
    "claimed",
    "processing",
    "succeeded",
    "retry_scheduled",
    "failed",
    "dead_letter",
    "cancelled",
    name="outbox_status",
    create_type=False,
)
EXECUTED_ACTION_STATUS = postgresql.ENUM(
    "started",
    "succeeded",
    "failed",
    "reversed",
    name="executed_action_status",
    create_type=False,
)
REFUND_ENTRY_TYPE = postgresql.ENUM(
    "refund", name="refund_entry_type", create_type=False
)

_ENUMS = (
    APPROVAL_STATUS,
    APPROVAL_DECISION_TYPE,
    OUTBOX_STATUS,
    EXECUTED_ACTION_STATUS,
    REFUND_ENTRY_TYPE,
)

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)
_NOW = sa.text("now()")


def upgrade() -> None:
    bind = op.get_bind()
    # Extend the shared workflow_state enum for support-ticket-v2 (v1 never uses these).
    for value in _NEW_WORKFLOW_STATES:
        op.execute(f"ALTER TYPE workflow_state ADD VALUE IF NOT EXISTS '{value}'")
    for enum in _ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("proposed_action_id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("requester_user_id", sa.UUID(), nullable=False),
        sa.Column("status", APPROVAL_STATUS, nullable=False),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("risk_level", sa.String(length=24), nullable=False),
        sa.Column("required_role", sa.String(length=24), nullable=True),
        sa.Column("requested_amount_pence", sa.Integer(), nullable=True),
        sa.Column("maximum_allowed_amount_pence", sa.Integer(), nullable=True),
        sa.Column("approved_amount_pence", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("policy_citation_ids", _JSONB, nullable=False),
        sa.Column("policy_version_ids", _JSONB, nullable=False),
        sa.Column("rule_result_json", _JSONB, nullable=False),
        sa.Column("rule_result_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_snapshot_json", _JSONB, nullable=False),
        sa.Column("evidence_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("draft_response_subject", sa.Text(), nullable=False),
        sa.Column("draft_response_body", sa.Text(), nullable=False),
        sa.Column("request_reason", sa.Text(), nullable=True),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("decided_at", _TS, nullable=True),
        sa.Column("execution_started_at", _TS, nullable=True),
        sa.Column("executed_at", _TS, nullable=True),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.Column("updated_at", _TS, server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "requested_amount_pence IS NULL OR requested_amount_pence > 0",
            name="ck_approval_requested_amount_positive",
        ),
        sa.CheckConstraint(
            "approved_amount_pence IS NULL OR maximum_allowed_amount_pence IS NULL "
            "OR approved_amount_pence <= maximum_allowed_amount_pence",
            name="ck_approval_amount_within_max",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_approval_expiry_after_creation"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["proposed_action_id"], ["proposed_actions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["requester_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_approval_open_per_action",
        "approval_requests",
        ["proposed_action_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])
    op.create_index(
        "ix_approval_requests_run", "approval_requests", ["workflow_run_id"]
    )
    op.create_index("ix_approval_requests_ticket", "approval_requests", ["ticket_id"])
    op.create_index(
        "ix_approval_requests_requester", "approval_requests", ["requester_user_id"]
    )

    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("approval_request_id", sa.UUID(), nullable=False),
        sa.Column("decision", APPROVAL_DECISION_TYPE, nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_role", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("previous_status", sa.String(length=24), nullable=False),
        sa.Column("new_status", sa.String(length=24), nullable=False),
        sa.Column("requested_amount_pence", sa.Integer(), nullable=True),
        sa.Column("decided_amount_pence", sa.Integer(), nullable=True),
        sa.Column("decision_metadata_json", _JSONB, nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_request_id"], ["approval_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approval_decisions_request", "approval_decisions", ["approval_request_id"]
    )

    op.create_table(
        "outbox_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("approval_request_id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("proposed_action_id", sa.UUID(), nullable=False),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("payload_version", sa.String(length=20), nullable=False),
        sa.Column("payload_json", _JSONB, nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("status", OUTBOX_STATUS, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("maximum_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", _TS, nullable=False),
        sa.Column("claimed_at", _TS, nullable=True),
        sa.Column("claimed_by", sa.String(length=80), nullable=True),
        sa.Column("lease_expires_at", _TS, nullable=True),
        sa.Column("last_error_code", sa.String(length=48), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", _TS, nullable=True),
        sa.Column("dead_lettered_at", _TS, nullable=True),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.Column("updated_at", _TS, server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "maximum_attempts > 0", name="ck_outbox_max_attempts_positive"
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_attempts_nonneg"),
        sa.ForeignKeyConstraint(
            ["approval_request_id"], ["approval_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["proposed_action_id"], ["proposed_actions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_idempotency_key"),
        sa.UniqueConstraint("approval_request_id", name="uq_outbox_per_approval"),
    )
    op.create_index("ix_outbox_status", "outbox_jobs", ["status"])
    op.create_index(
        "ix_outbox_claim_order",
        "outbox_jobs",
        ["status", "priority", "next_attempt_at", "created_at"],
    )

    op.create_table(
        "executed_actions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("approval_request_id", sa.UUID(), nullable=False),
        sa.Column("outbox_job_id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("status", EXECUTED_ACTION_STATUS, nullable=False),
        sa.Column("amount_pence", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("business_effect_reference", sa.String(length=64), nullable=False),
        sa.Column("precondition_snapshot_json", _JSONB, nullable=False),
        sa.Column("precondition_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("result_json", _JSONB, nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("executed_by", sa.String(length=80), nullable=False),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("completed_at", _TS, nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint(
            "amount_pence IS NULL OR amount_pence > 0",
            name="ck_executed_amount_positive",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"], ["approval_requests.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["outbox_job_id"], ["outbox_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_executed_actions_idempotency"),
        sa.UniqueConstraint("outbox_job_id", name="uq_executed_actions_outbox"),
    )
    op.create_index("ix_executed_actions_order", "executed_actions", ["order_id"])
    op.create_index(
        "ix_executed_actions_approval", "executed_actions", ["approval_request_id"]
    )

    op.create_table(
        "refund_ledger_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("executed_action_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("order_item_id", sa.UUID(), nullable=True),
        sa.Column("amount_pence", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("entry_type", REFUND_ENTRY_TYPE, nullable=False),
        sa.Column("reference", sa.String(length=64), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint("amount_pence > 0", name="ck_ledger_amount_positive"),
        sa.ForeignKeyConstraint(
            ["executed_action_id"], ["executed_actions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["order_item_id"], ["order_items.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("executed_action_id", name="uq_ledger_executed_action"),
        sa.UniqueConstraint("idempotency_key", name="uq_ledger_idempotency"),
    )
    op.create_index("ix_refund_ledger_order", "refund_ledger_entries", ["order_id"])


def downgrade() -> None:
    op.drop_table("refund_ledger_entries")
    op.drop_table("executed_actions")
    op.drop_table("outbox_jobs")
    op.drop_table("approval_decisions")
    op.drop_index("uq_approval_open_per_action", table_name="approval_requests")
    op.drop_table("approval_requests")

    bind = op.get_bind()
    for enum in reversed(_ENUMS):
        enum.drop(bind, checkfirst=True)
    # workflow_state enum values added in upgrade() cannot be removed (PG limitation).
