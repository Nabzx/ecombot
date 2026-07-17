"""s4 model layer

Revision ID: a1f4c7e21b90
Revises: bc0f537763c1
Create Date: 2026-07-17 09:00:00.000000

Adds prompt-version and model-call persistence plus their enum types. Existing data is
untouched. Downgrade drops the tables and the enum types it created.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1f4c7e21b90"
down_revision: str | None = "bc0f537763c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MODEL_TASK_TYPE = postgresql.ENUM(
    "ticket_classification",
    "identifier_extraction",
    "read_only_tool_planning",
    "evidence_summary",
    "response_drafting",
    "decision_summary",
    "structured_output_repair",
    name="model_task_type",
    create_type=False,
)
PROMPT_STATUS = postgresql.ENUM(
    "draft",
    "active",
    "deprecated",
    name="prompt_status",
    create_type=False,
)
MODEL_CALL_STATUS = postgresql.ENUM(
    "succeeded",
    "repaired",
    "failed",
    name="model_call_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    # Enum types are created explicitly (create_type=False on the columns) so the
    # migration is safe on empty and existing databases.
    MODEL_TASK_TYPE.create(bind, checkfirst=True)
    PROMPT_STATUS.create(bind, checkfirst=True)
    MODEL_CALL_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("semantic_version", sa.String(length=20), nullable=False),
        sa.Column("task_type", MODEL_TASK_TYPE, nullable=False),
        sa.Column("status", PROMPT_STATUS, nullable=False),
        sa.Column("template_hash", sa.String(length=64), nullable=False),
        sa.Column("system_template", sa.Text(), nullable=False),
        sa.Column("user_template", sa.Text(), nullable=False),
        sa.Column("input_schema_name", sa.String(length=100), nullable=False),
        sa.Column("output_schema_name", sa.String(length=100), nullable=True),
        sa.Column(
            "configuration_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "name", "semantic_version", name="uq_prompt_versions_name_version"
        ),
    )
    op.create_index(
        "ix_prompt_versions_task_status",
        "prompt_versions",
        ["task_type", "status"],
    )

    op.create_table(
        "model_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=True),
        sa.Column("workflow_run_id", sa.UUID(), nullable=True),
        sa.Column("task_type", MODEL_TASK_TYPE, nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version_id", sa.UUID(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("status", MODEL_CALL_STATUS, nullable=False),
        sa.Column("input_token_count", sa.Integer(), nullable=False),
        sa.Column("output_token_count", sa.Integer(), nullable=False),
        sa.Column("total_token_count", sa.Integer(), nullable=False),
        sa.Column("token_source", sa.String(length=24), nullable=False),
        sa.Column("estimated_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("cost_currency", sa.String(length=3), nullable=False),
        sa.Column("cost_status", sa.String(length=24), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("finish_reason", sa.String(length=24), nullable=False),
        sa.Column("repair_count", sa.Integer(), nullable=False),
        sa.Column("fallback_from_provider", sa.String(length=40), nullable=True),
        sa.Column("fallback_reason", sa.String(length=200), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "redacted_input_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "parsed_output_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("raw_output_redacted", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"], ["prompt_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_calls_task_type", "model_calls", ["task_type"])
    op.create_index(
        "ix_model_calls_provider_model", "model_calls", ["provider", "model"]
    )
    op.create_index(
        "ix_model_calls_prompt_version", "model_calls", ["prompt_version_id"]
    )
    op.create_index("ix_model_calls_ticket", "model_calls", ["ticket_id"])
    op.create_index("ix_model_calls_correlation", "model_calls", ["correlation_id"])
    op.create_index("ix_model_calls_status", "model_calls", ["status"])
    op.create_index("ix_model_calls_created_at", "model_calls", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_model_calls_created_at", table_name="model_calls")
    op.drop_index("ix_model_calls_status", table_name="model_calls")
    op.drop_index("ix_model_calls_correlation", table_name="model_calls")
    op.drop_index("ix_model_calls_ticket", table_name="model_calls")
    op.drop_index("ix_model_calls_prompt_version", table_name="model_calls")
    op.drop_index("ix_model_calls_provider_model", table_name="model_calls")
    op.drop_index("ix_model_calls_task_type", table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_index("ix_prompt_versions_task_status", table_name="prompt_versions")
    op.drop_table("prompt_versions")

    bind = op.get_bind()
    MODEL_CALL_STATUS.drop(bind, checkfirst=True)
    PROMPT_STATUS.drop(bind, checkfirst=True)
    MODEL_TASK_TYPE.drop(bind, checkfirst=True)
