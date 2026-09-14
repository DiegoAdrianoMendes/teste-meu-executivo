"""add legal_demands (Onda 2 — Fundação jurídica: modelo canônico)

Camada canônica "Demanda jurídica" (Plano Mestre §5) que unifica as 7
naturezas atendidas pelo escritório (judicial, administrativo,
consultivo, contrato, acordo, serviço recorrente, massificado) sem
duplicar o que já existe em `matters`/`finance_entries`/
`contract_billing_proposals` — `matter_id`/`contract_ref`/`finance_ref`
referenciam essas tabelas quando aplicável.

Revision ID: 013d26e9d488
Revises: 58f5e2e75303
Create Date: 2026-07-26 00:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "013d26e9d488"
down_revision: str | Sequence[str] | None = "58f5e2e75303"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLE_NAME = "legal_demands"

_NATURE_VALUES = (
    "judicial",
    "administrativo",
    "consultivo",
    "contrato",
    "acordo",
    "servico_recorrente",
    "massificado",
)
_RISK_VALUES = ("baixo", "medio", "alto", "critico")
_PHASE_VALUES = ("triagem", "em_andamento", "suspenso", "concluido", "encerrado")


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "{TABLE_NAME}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{TABLE_NAME}"')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("external_ref", sa.String(length=160), nullable=True),
        sa.Column(
            "nature",
            sa.Enum(*_NATURE_VALUES, name="legal_demand_nature_enum"),
            nullable=False,
        ),
        sa.Column("area", sa.String(length=80), nullable=True),
        sa.Column("subarea", sa.String(length=100), nullable=True),
        sa.Column("client_id", FlexibleUUID(), nullable=False),
        sa.Column("parties", sa.JSON(), nullable=False),
        sa.Column("unit", sa.String(length=120), nullable=True),
        sa.Column("team", sa.String(length=120), nullable=True),
        sa.Column("responsible_user_id", FlexibleUUID(), nullable=True),
        sa.Column("strategy", sa.Text(), nullable=True),
        sa.Column(
            "risk",
            sa.Enum(*_RISK_VALUES, name="legal_demand_risk_enum"),
            nullable=False,
            server_default="medio",
        ),
        sa.Column("value", sa.Numeric(15, 2), nullable=True),
        sa.Column(
            "phase",
            sa.Enum(*_PHASE_VALUES, name="legal_demand_phase_enum"),
            nullable=False,
            server_default="triagem",
        ),
        sa.Column("sla", sa.JSON(), nullable=False),
        sa.Column("playbook_ref", sa.String(length=160), nullable=True),
        sa.Column("documents_required", sa.JSON(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("contract_ref", FlexibleUUID(), nullable=True),
        sa.Column("finance_ref", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["responsible_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["contract_ref"], ["contract_billing_proposals.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["finance_ref"], ["finance_entries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "external_ref",
            name="uq_legal_demands_workspace_external_ref",
        ),
    )
    op.create_index(f"ix_{TABLE_NAME}_workspace_id", TABLE_NAME, ["workspace_id"])
    op.create_index(f"ix_{TABLE_NAME}_nature", TABLE_NAME, ["nature"])
    op.create_index(f"ix_{TABLE_NAME}_area", TABLE_NAME, ["area"])
    op.create_index(f"ix_{TABLE_NAME}_client_id", TABLE_NAME, ["client_id"])
    op.create_index(f"ix_{TABLE_NAME}_responsible_user_id", TABLE_NAME, ["responsible_user_id"])
    op.create_index(f"ix_{TABLE_NAME}_phase", TABLE_NAME, ["phase"])
    op.create_index(f"ix_{TABLE_NAME}_matter_id", TABLE_NAME, ["matter_id"])
    op.create_index(f"ix_{TABLE_NAME}_contract_ref", TABLE_NAME, ["contract_ref"])
    op.create_index(f"ix_{TABLE_NAME}_finance_ref", TABLE_NAME, ["finance_ref"])
    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    op.drop_index(f"ix_{TABLE_NAME}_finance_ref", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_contract_ref", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_matter_id", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_phase", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_responsible_user_id", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_client_id", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_area", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_nature", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_workspace_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS legal_demand_phase_enum")
        op.execute("DROP TYPE IF EXISTS legal_demand_risk_enum")
        op.execute("DROP TYPE IF EXISTS legal_demand_nature_enum")
