"""Add canonical legal client contracts.

Revision ID: yz7s0p4q5l86
Revises: xy6r9n2p3k75
"""

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision = "yz7s0p4q5l86"
down_revision = "xy6r9n2p3k75"
branch_labels = None
depends_on = None


def upgrade() -> None:
    status_enum = sa.Enum(
        "draft", "active", "ended", "cancelled", name="client_contract_status_enum"
    )
    op.create_table(
        "client_contracts",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID()),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="draft"),
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_until", sa.Date()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "id", name="uq_client_contracts_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_client_contracts_workspace_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_client_contracts_workspace_matter",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_client_contracts_workspace_client_status",
        "client_contracts",
        ["workspace_id", "client_id", "status"],
    )
    op.create_foreign_key(
        "fk_tasks_workspace_client_contract",
        "tasks",
        "client_contracts",
        ["workspace_id", "contract_id"],
        ["workspace_id", "id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_tasks_workspace_client_contract", "tasks", type_="foreignkey")
    op.drop_index("ix_client_contracts_workspace_client_status", table_name="client_contracts")
    op.drop_table("client_contracts")
    sa.Enum(name="client_contract_status_enum").drop(op.get_bind(), checkfirst=True)
