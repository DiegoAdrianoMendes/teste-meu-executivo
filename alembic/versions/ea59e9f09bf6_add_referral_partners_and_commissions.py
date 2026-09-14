"""add referral_partners, matter_referrals and referral_commissions

Revision ID: ea59e9f09bf6
Revises: f8a2d443a6ee
Create Date: 2026-07-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "ea59e9f09bf6"
down_revision: str | Sequence[str] | None = "f8a2d443a6ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = (
    "referral_partners",
    "matter_referrals",
    "referral_commissions",
)


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
        "referral_partners",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum(
                "contato", "cliente", "parceiro", "advogado", name="referral_partner_type_enum"
            ),
            nullable=False,
            server_default="contato",
        ),
        sa.Column("contato_ref", sa.String(length=200), nullable=True),
        sa.Column(
            "comissao_tipo",
            sa.Enum("percentual", "fixo", name="referral_commission_type_enum"),
            nullable=False,
            server_default="percentual",
        ),
        sa.Column("comissao_valor", sa.Numeric(15, 2), nullable=False),
        sa.Column("vigencia_inicio", sa.Date(), nullable=False),
        sa.Column("vigencia_fim", sa.Date(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_referral_partners_workspace_id", "referral_partners", ["workspace_id"])
    op.create_index(
        "ix_referral_partners_workspace_ativo", "referral_partners", ["workspace_id", "ativo"]
    )

    op.create_table(
        "matter_referrals",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=False),
        sa.Column("partner_id", FlexibleUUID(), nullable=False),
        sa.Column("registrado_por", FlexibleUUID(), nullable=True),
        sa.Column("data", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["partner_id"], ["referral_partners.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("matter_id", name="uq_matter_referrals_matter_id"),
    )
    op.create_index("ix_matter_referrals_workspace_id", "matter_referrals", ["workspace_id"])
    op.create_index("ix_matter_referrals_matter_id", "matter_referrals", ["matter_id"])
    op.create_index("ix_matter_referrals_partner_id", "matter_referrals", ["partner_id"])

    op.create_table(
        "referral_commissions",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=False),
        sa.Column("partner_id", FlexibleUUID(), nullable=False),
        sa.Column("charge_id", FlexibleUUID(), nullable=False),
        sa.Column("expense_entry_id", FlexibleUUID(), nullable=False),
        sa.Column("valor_base", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "comissao_tipo",
            sa.Enum(
                "percentual", "fixo", name="referral_commission_type_enum", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("comissao_valor_calculado", sa.Numeric(15, 2), nullable=False),
        sa.Column("commission_metadata", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["partner_id"], ["referral_partners.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["charge_id"], ["finance_charges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["expense_entry_id"], ["finance_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "charge_id", name="uq_referral_commissions_workspace_charge"
        ),
    )
    op.create_index("ix_referral_commissions_workspace_id", "referral_commissions", ["workspace_id"])
    op.create_index("ix_referral_commissions_matter_id", "referral_commissions", ["matter_id"])
    op.create_index("ix_referral_commissions_partner_id", "referral_commissions", ["partner_id"])
    op.create_index("ix_referral_commissions_charge_id", "referral_commissions", ["charge_id"])
    op.create_index(
        "ix_referral_commissions_expense_entry_id", "referral_commissions", ["expense_entry_id"]
    )

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("ix_referral_commissions_expense_entry_id", table_name="referral_commissions")
    op.drop_index("ix_referral_commissions_charge_id", table_name="referral_commissions")
    op.drop_index("ix_referral_commissions_partner_id", table_name="referral_commissions")
    op.drop_index("ix_referral_commissions_matter_id", table_name="referral_commissions")
    op.drop_index("ix_referral_commissions_workspace_id", table_name="referral_commissions")
    op.drop_table("referral_commissions")

    op.drop_index("ix_matter_referrals_partner_id", table_name="matter_referrals")
    op.drop_index("ix_matter_referrals_matter_id", table_name="matter_referrals")
    op.drop_index("ix_matter_referrals_workspace_id", table_name="matter_referrals")
    op.drop_table("matter_referrals")

    op.drop_index("ix_referral_partners_workspace_ativo", table_name="referral_partners")
    op.drop_index("ix_referral_partners_workspace_id", table_name="referral_partners")
    op.drop_table("referral_partners")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS referral_commission_type_enum")
        op.execute("DROP TYPE IF EXISTS referral_partner_type_enum")
