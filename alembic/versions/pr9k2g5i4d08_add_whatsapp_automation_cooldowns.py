"""add persistent WhatsApp automation cooldowns

Revision ID: pr9k2g5i4d08
Revises: oq8j1f4h3c97
"""

import sqlalchemy as sa

from alembic import op

revision = "pr9k2g5i4d08"
down_revision = "oq8j1f4h3c97"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_automation_rule_dispatches",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.String(length=120), nullable=False),
        sa.Column(
            "last_dispatched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("dispatch_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["contact_id"], ["whatsapp_contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "contact_id",
            "rule_id",
            name="uq_wa_automation_dispatch_workspace_contact_rule",
        ),
    )
    op.create_index(
        "ix_whatsapp_automation_rule_dispatches_workspace_id",
        "whatsapp_automation_rule_dispatches",
        ["workspace_id"],
    )
    op.create_index(
        "ix_whatsapp_automation_rule_dispatches_contact_id",
        "whatsapp_automation_rule_dispatches",
        ["contact_id"],
    )
    op.create_index(
        "ix_wa_automation_dispatch_workspace_last",
        "whatsapp_automation_rule_dispatches",
        ["workspace_id", "last_dispatched_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wa_automation_dispatch_workspace_last", table_name="whatsapp_automation_rule_dispatches"
    )
    op.drop_index(
        "ix_whatsapp_automation_rule_dispatches_contact_id",
        table_name="whatsapp_automation_rule_dispatches",
    )
    op.drop_index(
        "ix_whatsapp_automation_rule_dispatches_workspace_id",
        table_name="whatsapp_automation_rule_dispatches",
    )
    op.drop_table("whatsapp_automation_rule_dispatches")
