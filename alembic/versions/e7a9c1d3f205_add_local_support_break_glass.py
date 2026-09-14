"""Add local support break-glass grants and immutable audit events.

Revision ID: e7a9c1d3f205
Revises: d6f8a0c2e105
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "e7a9c1d3f205"
down_revision: str | Sequence[str] | None = "d6f8a0c2e105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

support_access_state_enum = sa.Enum(
    "requested",
    "approved",
    "active",
    "expired",
    "revoked",
    "rejected",
    name="support_access_state_enum",
    native_enum=False,
    create_constraint=True,
)


def _create_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION prevent_support_access_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'support_access_events are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_support_access_events_immutable
            BEFORE UPDATE OR DELETE ON support_access_events
            FOR EACH ROW EXECUTE FUNCTION prevent_support_access_event_mutation()
            """
        )
        return
    if dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_support_access_events_no_update
            BEFORE UPDATE ON support_access_events
            BEGIN
                SELECT RAISE(ABORT, 'support_access_events are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_support_access_events_no_delete
            BEFORE DELETE ON support_access_events
            BEGIN
                SELECT RAISE(ABORT, 'support_access_events are immutable');
            END
            """
        )
        return
    raise RuntimeError(f"Dialeto não suportado para suporte break-glass: {dialect}")


def _drop_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_support_access_events_immutable "
            "ON support_access_events"
        )
        op.execute("DROP FUNCTION IF EXISTS prevent_support_access_event_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_support_access_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_support_access_events_no_delete")


def upgrade() -> None:
    op.create_table(
        "support_access_grants",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("requester_user_id", FlexibleUUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("ticket_reference", sa.String(length=120), nullable=False),
        sa.Column("requested_scopes", FlexibleJSON(), nullable=False),
        sa.Column("requested_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("state", support_access_state_enum, nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("approver_user_id", FlexibleUUID(), nullable=True),
        sa.Column("approved_scopes", FlexibleJSON(), nullable=True),
        sa.Column("approved_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("activation_admin_session_id", FlexibleUUID(), nullable=True),
        sa.Column("active_token_jti", sa.String(length=240), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("revoked_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "requested_duration_minutes BETWEEN 5 AND 30",
            name="ck_support_access_requested_duration",
        ),
        sa.CheckConstraint(
            "approved_duration_minutes IS NULL OR "
            "(approved_duration_minutes >= 1 AND "
            "approved_duration_minutes <= requested_duration_minutes)",
            name="ck_support_access_approved_duration",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) >= 15",
            name="ck_support_access_reason_required",
        ),
        sa.CheckConstraint(
            "length(trim(ticket_reference)) >= 3",
            name="ck_support_access_ticket_required",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requester_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approver_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["activated_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["activation_admin_session_id"],
            ["administrative_sessions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rejected_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "requester_user_id",
            "ticket_reference",
            name="uq_support_access_grant_request_ticket",
        ),
        sa.UniqueConstraint(
            "active_token_jti",
            name="uq_support_access_grant_active_token_jti",
        ),
    )
    op.create_index(
        "ix_support_access_workspace_state_requested",
        "support_access_grants",
        ["workspace_id", "state", "requested_at"],
    )
    op.create_index(
        "ix_support_access_requester_state_requested",
        "support_access_grants",
        ["requester_user_id", "state", "requested_at"],
    )
    op.create_index(
        "ix_support_access_state_expires",
        "support_access_grants",
        ["state", "expires_at"],
    )

    op.create_table(
        "support_access_events",
        sa.Column("grant_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("from_state", sa.String(length=16), nullable=True),
        sa.Column("to_state", sa.String(length=16), nullable=True),
        sa.Column(
            "details",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["support_access_grants.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_support_access_events_grant_occurred",
        "support_access_events",
        ["grant_id", "occurred_at"],
    )
    op.create_index(
        "ix_support_access_events_workspace_occurred",
        "support_access_events",
        ["workspace_id", "occurred_at"],
    )
    _create_immutability_guards()


def downgrade() -> None:
    _drop_immutability_guards()
    op.drop_table("support_access_events")
    op.drop_table("support_access_grants")
