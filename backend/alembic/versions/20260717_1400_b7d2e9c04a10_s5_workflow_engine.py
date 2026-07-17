"""s5 workflow engine

Revision ID: b7d2e9c04a10
Revises: a1f4c7e21b90
Create Date: 2026-07-17 14:00:00.000000

Adds workflow-run/checkpoint/step/tool-call/proposed-action tables and their enums, plus
the model-call workflow foreign keys. Existing S0-S4 data is untouched. Downgrade drops the
new tables, columns and enum types.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d2e9c04a10"
down_revision: str | None = "a1f4c7e21b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


WORKFLOW_STATE = postgresql.ENUM(
    "received",
    "validating",
    "sanitising",
    "classifying",
    "extracting_identifiers",
    "resolving_customer",
    "resolving_order",
    "retrieving_order_data",
    "retrieving_policy",
    "evaluating_rules",
    "summarising_evidence",
    "drafting_response",
    "calculating_route",
    "awaiting_agent",
    "awaiting_approval",
    "needs_information",
    "escalated",
    "blocked",
    "failed_validation",
    "failed_dependency",
    "failed_model",
    "cancelled",
    "resolved_without_action",
    name="workflow_state",
    create_type=False,
)
WORKFLOW_STATUS = postgresql.ENUM(
    "pending",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
    name="workflow_status",
    create_type=False,
)
TRIGGER_TYPE = postgresql.ENUM(
    "ticket_received",
    "manual_reprocess",
    "evaluation",
    "replay",
    name="workflow_trigger_type",
    create_type=False,
)
FAILURE_CODE = postgresql.ENUM(
    "validation_failed",
    "dependency_unavailable",
    "model_failed",
    "ownership_blocked",
    "deadline_exceeded",
    "step_limit_exceeded",
    "checkpoint_invalid",
    "cancelled",
    "internal_error",
    name="workflow_failure_code",
    create_type=False,
)
STEP_STATUS = postgresql.ENUM(
    "started",
    "completed",
    "failed",
    name="workflow_step_status",
    create_type=False,
)
PROPOSED_ACTION_STATUS = postgresql.ENUM(
    "draft",
    "ready_for_agent",
    "awaiting_approval",
    "blocked",
    "superseded",
    "cancelled",
    name="proposed_action_status",
    create_type=False,
)

_ENUMS = (
    WORKFLOW_STATE,
    WORKFLOW_STATUS,
    TRIGGER_TYPE,
    FAILURE_CODE,
    STEP_STATUS,
    PROPOSED_ACTION_STATUS,
)

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)
_NOW = sa.text("now()")


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_name", sa.String(length=60), nullable=False),
        sa.Column("workflow_version", sa.String(length=20), nullable=False),
        sa.Column("state_schema_version", sa.String(length=40), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("status", WORKFLOW_STATUS, nullable=False),
        sa.Column("current_state", WORKFLOW_STATE, nullable=False),
        sa.Column("current_step", sa.String(length=60), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("trigger_type", TRIGGER_TYPE, nullable=False),
        sa.Column("initiated_by_user_id", sa.UUID(), nullable=True),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("finished_at", _TS, nullable=True),
        sa.Column("last_checkpoint_id", sa.UUID(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("resume_count", sa.Integer(), nullable=False),
        sa.Column("replay_source_run_id", sa.UUID(), nullable=True),
        sa.Column("failure_code", FAILURE_CODE, nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", _JSONB, nullable=False),
        sa.Column("claimed_by", sa.String(length=80), nullable=True),
        sa.Column("claim_expires_at", _TS, nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.Column("updated_at", _TS, server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["initiated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["replay_source_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("correlation_id", name="uq_workflow_runs_correlation"),
    )
    op.create_index("ix_workflow_runs_state", "workflow_runs", ["current_state"])
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_index("ix_workflow_runs_ticket", "workflow_runs", ["ticket_id"])
    op.create_index("ix_workflow_runs_created_at", "workflow_runs", ["created_at"])
    # At most one non-terminal run per ticket + workflow version.
    op.create_index(
        "uq_workflow_runs_active_ticket",
        "workflow_runs",
        ["ticket_id", "workflow_name", "workflow_version"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'running', 'paused') " "AND trigger_type <> 'replay'"
        ),
    )

    op.create_table(
        "workflow_checkpoints",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("state", WORKFLOW_STATE, nullable=False),
        sa.Column("state_schema_version", sa.String(length=40), nullable=False),
        sa.Column("snapshot_json", _JSONB, nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_run_id", "step_index", name="uq_workflow_checkpoints_run_step"
        ),
    )
    op.create_index(
        "ix_workflow_checkpoints_run", "workflow_checkpoints", ["workflow_run_id"]
    )

    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(length=60), nullable=False),
        sa.Column("source_state", WORKFLOW_STATE, nullable=False),
        sa.Column("destination_state", WORKFLOW_STATE, nullable=True),
        sa.Column("status", STEP_STATUS, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("finished_at", _TS, nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("input_summary_json", _JSONB, nullable=False),
        sa.Column("output_summary_json", _JSONB, nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("model_call_ids", _JSONB, nullable=False),
        sa.Column("tool_call_ids", _JSONB, nullable=False),
        sa.Column("citation_ids", _JSONB, nullable=False),
        sa.Column("metadata_json", _JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_steps_run", "workflow_steps", ["workflow_run_id"])
    op.create_index(
        "ix_workflow_steps_run_index",
        "workflow_steps",
        ["workflow_run_id", "step_index"],
    )

    op.create_table(
        "workflow_tool_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("workflow_step_id", sa.UUID(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("tool_version", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input_json", _JSONB, nullable=False),
        sa.Column("output_json", _JSONB, nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_step_id"], ["workflow_steps.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_tool_calls_run", "workflow_tool_calls", ["workflow_run_id"]
    )
    op.create_index(
        "ix_workflow_tool_calls_step", "workflow_tool_calls", ["workflow_step_id"]
    )

    op.create_table(
        "proposed_actions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("status", PROPOSED_ACTION_STATUS, nullable=False),
        sa.Column("risk_level", sa.String(length=24), nullable=False),
        sa.Column("required_role", sa.String(length=24), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("amount_pence", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("draft_response_subject", sa.Text(), nullable=False),
        sa.Column("draft_response_body", sa.Text(), nullable=False),
        sa.Column("citation_ids", _JSONB, nullable=False),
        sa.Column("rule_result_json", _JSONB, nullable=False),
        sa.Column("decision_summary_json", _JSONB, nullable=False),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.Column("updated_at", _TS, server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_proposed_actions_run", "proposed_actions", ["workflow_run_id"])
    op.create_index("ix_proposed_actions_ticket", "proposed_actions", ["ticket_id"])
    op.create_index("ix_proposed_actions_status", "proposed_actions", ["status"])

    # Link model calls to the workflow engine (workflow_run_id already exists in S4).
    op.add_column(
        "model_calls", sa.Column("workflow_step_id", sa.UUID(), nullable=True)
    )
    # Null any pre-existing workflow_run_id values (they reference a prior, now-dropped
    # generation of workflow_runs) so adding the FK is safe on a re-upgraded database.
    op.execute("UPDATE model_calls SET workflow_run_id = NULL")
    op.create_foreign_key(
        "fk_model_calls_workflow_run",
        "model_calls",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_model_calls_workflow_step",
        "model_calls",
        "workflow_steps",
        ["workflow_step_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_model_calls_workflow_run", "model_calls", ["workflow_run_id"])


def downgrade() -> None:
    op.drop_index("ix_model_calls_workflow_run", table_name="model_calls")
    op.drop_constraint(
        "fk_model_calls_workflow_step", "model_calls", type_="foreignkey"
    )
    op.drop_constraint("fk_model_calls_workflow_run", "model_calls", type_="foreignkey")
    op.drop_column("model_calls", "workflow_step_id")

    op.drop_table("proposed_actions")
    op.drop_table("workflow_tool_calls")
    op.drop_table("workflow_steps")
    op.drop_table("workflow_checkpoints")
    op.drop_index("uq_workflow_runs_active_ticket", table_name="workflow_runs")
    op.drop_table("workflow_runs")

    bind = op.get_bind()
    for enum in reversed(_ENUMS):
        enum.drop(bind, checkfirst=True)
