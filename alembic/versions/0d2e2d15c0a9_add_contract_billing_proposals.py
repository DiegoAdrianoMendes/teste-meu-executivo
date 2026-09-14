"""add contract_billing_proposals

Revision ID: 0d2e2d15c0a9
Revises: 8ecdc71ea65b
Create Date: 2026-07-25 16:30:00.000000

F11 — Contrato → nós financeiros com aprovação: `contract_billing_proposals`
guarda o plano de cobrança extraído de um contrato (IA) como PROPOSTA
(status draft|pending_approval|approved|rejected). Nenhum FinanceEntry/
FinanceRecurrence/FeeArrangement nasce aqui — só na aprovação humana via
`ContractBillingService.approve_proposal`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "0d2e2d15c0a9"
down_revision: str | Sequence[str] | None = "8ecdc71ea65b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = ("contract_billing_proposals",)


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in TABLES:
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
    for table_name in TABLES:
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        "contract_billing_proposals",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=False),
        sa.Column("source_document_name", sa.String(255), nullable=True),
        sa.Column("items", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "pending_approval",
                "approved",
                "rejected",
                name="contract_billing_proposal_status_enum",
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("extraction_model", sa.String(120), nullable=True),
        sa.Column("approval_id", FlexibleUUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", FlexibleUUID(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_id"], ["approval_queue_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_contract_billing_proposals_workspace_id", "contract_billing_proposals", ["workspace_id"]
    )
    op.create_index(
        "ix_contract_billing_proposals_matter_id", "contract_billing_proposals", ["matter_id"]
    )
    op.create_index(
        "ix_contract_billing_proposals_status", "contract_billing_proposals", ["status"]
    )
    op.create_index(
        "ix_contract_billing_proposals_approval_id", "contract_billing_proposals", ["approval_id"]
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("ix_contract_billing_proposals_approval_id", table_name="contract_billing_proposals")
    op.drop_index("ix_contract_billing_proposals_status", table_name="contract_billing_proposals")
    op.drop_index("ix_contract_billing_proposals_matter_id", table_name="contract_billing_proposals")
    op.drop_index("ix_contract_billing_proposals_workspace_id", table_name="contract_billing_proposals")
    op.drop_table("contract_billing_proposals")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS contract_billing_proposal_status_enum")
