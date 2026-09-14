"""merge Blackbox foundations and add canonical platform audit

Revision ID: e7a8c2f6d105
Revises: e5a8c2f6d104, e6a8c2f4d105
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a8c2f6d105"
down_revision: str | Sequence[str] | None = ("e5a8c2f6d104", "e6a8c2f4d105")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Converte a marca raiz legada para o contrato canônico. Depois desta
    # migration, o booleano isolado nunca mais é suficiente para o bypass.
    op.execute(
        """
        UPDATE users
        SET platform_role_package = 'platform_owner',
            is_internal_staff = true,
            platform_access_active = true
        WHERE is_platform_admin = true
        """
    )
    op.create_table(
        "platform_audit_events",
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_role_snapshot", sa.String(length=80), nullable=False),
        sa.Column("is_platform_root", sa.Boolean(), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_label_snapshot", sa.String(length=240), nullable=True),
        sa.Column("administrative_session_id", sa.Uuid(), nullable=True),
        sa.Column("session_type", sa.String(length=32), nullable=False),
        sa.Column("module", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=120), nullable=False),
        sa.Column("resource_id", sa.String(length=120), nullable=True),
        sa.Column("resource_label_snapshot", sa.String(length=240), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("changed_paths", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reason_source", sa.String(length=16), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column("operation_id", sa.String(length=120), nullable=False),
        sa.Column("parent_event_id", sa.Uuid(), nullable=True),
        sa.Column("bulk_job_id", sa.Uuid(), nullable=True),
        sa.Column("restoration_of_event_id", sa.Uuid(), nullable=True),
        sa.Column("resource_version_before", sa.Integer(), nullable=True),
        sa.Column("resource_version_after", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("route", sa.String(length=320), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["administrative_session_id"],
            ["administrative_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_event_id"], ["platform_audit_events.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["restoration_of_event_id"],
            ["platform_audit_events.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["subject_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_audit_occurred_id",
        "platform_audit_events",
        ["occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_platform_audit_actor_occurred",
        "platform_audit_events",
        ["actor_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_platform_audit_workspace_occurred",
        "platform_audit_events",
        ["workspace_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_platform_audit_resource",
        "platform_audit_events",
        ["resource_type", "resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_platform_audit_correlation",
        "platform_audit_events",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        "ix_platform_audit_operation",
        "platform_audit_events",
        ["operation_id"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_platform_audit_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'platform_audit_events is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_platform_audit_events_append_only
            BEFORE UPDATE OR DELETE ON platform_audit_events
            FOR EACH ROW EXECUTE FUNCTION reject_platform_audit_mutation();
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_platform_audit_events_append_only "
            "ON platform_audit_events"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_platform_audit_mutation()")
    op.drop_index("ix_platform_audit_operation", table_name="platform_audit_events")
    op.drop_index("ix_platform_audit_correlation", table_name="platform_audit_events")
    op.drop_index("ix_platform_audit_resource", table_name="platform_audit_events")
    op.drop_index("ix_platform_audit_workspace_occurred", table_name="platform_audit_events")
    op.drop_index("ix_platform_audit_actor_occurred", table_name="platform_audit_events")
    op.drop_index("ix_platform_audit_occurred_id", table_name="platform_audit_events")
    op.drop_table("platform_audit_events")
