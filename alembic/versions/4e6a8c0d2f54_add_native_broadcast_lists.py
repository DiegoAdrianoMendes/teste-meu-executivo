"""Add native broadcast audience lists and optional campaign flow binding.

Revision ID: 4e6a8c0d2f54
Revises: 3d5f7a9b1e43
Create Date: 2026-07-21 10:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "4e6a8c0d2f54"
down_revision: str | Sequence[str] | None = "3d5f7a9b1e43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIST_TABLE = "whatsapp_audience_lists"
MEMBER_TABLE = "whatsapp_audience_list_members"
WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in (LIST_TABLE, MEMBER_TABLE):
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in (MEMBER_TABLE, LIST_TABLE):
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        LIST_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("created_by_id", FlexibleUUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), server_default="manual", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("member_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("valid_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("invalid_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "import_metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','archived')",
            name="ck_wa_audience_lists_status",
        ),
        sa.CheckConstraint(
            "member_count >= 0 AND valid_count >= 0 AND invalid_count >= 0 AND duplicate_count >= 0",
            name="ck_wa_audience_lists_counts",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_wa_audience_lists_workspace_name"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_wa_audience_lists_workspace_id"),
    )
    op.create_index(f"ix_{LIST_TABLE}_workspace_id", LIST_TABLE, ["workspace_id"])
    op.create_index(f"ix_{LIST_TABLE}_created_by_id", LIST_TABLE, ["created_by_id"])
    op.create_index(
        "ix_wa_audience_lists_workspace_status",
        LIST_TABLE,
        ["workspace_id", "status"],
    )

    op.create_table(
        MEMBER_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("audience_list_id", FlexibleUUID(), nullable=False),
        sa.Column("contact_id", FlexibleUUID(), nullable=True),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="valid", nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column(
            "member_metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('valid','invalid','suppressed')",
            name="ck_wa_audience_members_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "audience_list_id"],
            [f"{LIST_TABLE}.workspace_id", f"{LIST_TABLE}.id"],
            name="fk_wa_audience_member_workspace_list",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["contact_id"], ["whatsapp_contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audience_list_id", "phone", name="uq_wa_audience_member_list_phone"),
    )
    for column in ("workspace_id", "audience_list_id", "contact_id", "matter_id"):
        op.create_index(f"ix_{MEMBER_TABLE}_{column}", MEMBER_TABLE, [column])
    op.create_index(
        "ix_wa_audience_members_workspace_status",
        MEMBER_TABLE,
        ["workspace_id", "status"],
    )

    with op.batch_alter_table("whatsapp_campaigns") as batch:
        batch.add_column(sa.Column("follow_up_flow_id", FlexibleUUID(), nullable=True))
        batch.add_column(
            sa.Column(
                "flow_start_mode",
                sa.String(length=24),
                server_default="manual",
                nullable=False,
            )
        )
        batch.create_foreign_key(
            "fk_wa_campaigns_follow_up_flow",
            "flows",
            ["follow_up_flow_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_wa_campaigns_flow_start_mode",
            "flow_start_mode IN ('manual','on_delivery','on_reply')",
        )
    op.create_index(
        "ix_whatsapp_campaigns_follow_up_flow_id",
        "whatsapp_campaigns",
        ["follow_up_flow_id"],
    )
    with op.batch_alter_table("whatsapp_campaign_recipients") as batch:
        batch.add_column(sa.Column("flow_run_id", FlexibleUUID(), nullable=True))
        batch.add_column(sa.Column("flow_trigger_event", sa.String(length=24), nullable=True))
        batch.add_column(sa.Column("flow_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_wa_campaign_recipients_flow_run",
            "flow_runs",
            ["flow_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_wa_campaign_recipients_flow_trigger_event",
            "flow_trigger_event IS NULL OR flow_trigger_event IN ('on_delivery','on_reply')",
        )
    op.create_index(
        "ix_whatsapp_campaign_recipients_flow_run_id",
        "whatsapp_campaign_recipients",
        ["flow_run_id"],
    )
    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    op.drop_index(
        "ix_whatsapp_campaign_recipients_flow_run_id",
        table_name="whatsapp_campaign_recipients",
    )
    with op.batch_alter_table("whatsapp_campaign_recipients") as batch:
        batch.drop_constraint(
            "ck_wa_campaign_recipients_flow_trigger_event",
            type_="check",
        )
        batch.drop_constraint("fk_wa_campaign_recipients_flow_run", type_="foreignkey")
        batch.drop_column("flow_started_at")
        batch.drop_column("flow_trigger_event")
        batch.drop_column("flow_run_id")
    op.drop_index("ix_whatsapp_campaigns_follow_up_flow_id", table_name="whatsapp_campaigns")
    with op.batch_alter_table("whatsapp_campaigns") as batch:
        batch.drop_constraint("ck_wa_campaigns_flow_start_mode", type_="check")
        batch.drop_constraint("fk_wa_campaigns_follow_up_flow", type_="foreignkey")
        batch.drop_column("flow_start_mode")
        batch.drop_column("follow_up_flow_id")
    op.drop_index("ix_wa_audience_members_workspace_status", table_name=MEMBER_TABLE)
    for column in reversed(("workspace_id", "audience_list_id", "contact_id", "matter_id")):
        op.drop_index(f"ix_{MEMBER_TABLE}_{column}", table_name=MEMBER_TABLE)
    op.drop_table(MEMBER_TABLE)
    op.drop_index("ix_wa_audience_lists_workspace_status", table_name=LIST_TABLE)
    op.drop_index(f"ix_{LIST_TABLE}_created_by_id", table_name=LIST_TABLE)
    op.drop_index(f"ix_{LIST_TABLE}_workspace_id", table_name=LIST_TABLE)
    op.drop_table(LIST_TABLE)
