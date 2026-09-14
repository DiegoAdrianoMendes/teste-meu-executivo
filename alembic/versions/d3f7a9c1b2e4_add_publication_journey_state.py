"""Jornada publicação→pasta — 19 estados em `process_publications` (Onda 4).

Revision ID: d3f7a9c1b2e4
Revises: a04b6e6f22b4
Create Date: 2026-07-27 00:00:00.000000

Estende `process_publications` (não recria) com `journey_state` (enum de 19
estados, CHECK constraint no padrão `native_enum=False` já usado pelos
outros enums do módulo — ver app/models/process_monitoring.py) e
`journey_updated_at`. A máquina de estados em si mora em
`app/services/legal_journey_service.py`; esta migração só carrega o dado.
Nenhuma tabela nova — RLS de `process_publications` (habilitada em
b6d8f0a2c415) já cobre a coluna nova, sem trabalho extra.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3f7a9c1b2e4"
down_revision: str | Sequence[str] | None = "a04b6e6f22b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOURNEY_STATES = (
    "capturada",
    "deduplicada",
    "vinculo_sugerido",
    "vinculada",
    "triagem_concluida",
    "prazo_sugerido",
    "prazo_confirmado",
    "trabalho_distribuido",
    "documentos_solicitados",
    "estrategia_preparada",
    "minuta_em_producao",
    "revisao_juridica",
    "correcoes_pendentes",
    "aprovada",
    "pasta_em_preparacao",
    "pasta_validada",
    "aguardando_protocolo_humano",
    "protocolo_registrado",
    "acompanhamento_criado",
)

CHECK_NAME = "publication_journey_state_enum"

SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _journey_state_check() -> str:
    allowed = ", ".join(f"'{value}'" for value in JOURNEY_STATES)
    return f"journey_state IN ({allowed})"


def upgrade() -> None:
    op.add_column(
        "process_publications",
        sa.Column(
            "journey_state",
            sa.String(length=40),
            nullable=False,
            server_default="capturada",
        ),
    )
    op.add_column(
        "process_publications",
        sa.Column("journey_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "process_publications",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch:
            batch.create_check_constraint(CHECK_NAME, _journey_state_check())
    else:
        op.create_check_constraint(CHECK_NAME, "process_publications", _journey_state_check())

    op.create_index(
        "ix_process_publication_workspace_journey_state",
        "process_publications",
        ["workspace_id", "journey_state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_process_publication_workspace_journey_state", table_name="process_publications"
    )

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "process_publications",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch:
            batch.drop_constraint(CHECK_NAME, type_="check")
            batch.drop_column("journey_updated_at")
            batch.drop_column("journey_state")
    else:
        op.drop_constraint(CHECK_NAME, "process_publications", type_="check")
        op.drop_column("process_publications", "journey_updated_at")
        op.drop_column("process_publications", "journey_state")
