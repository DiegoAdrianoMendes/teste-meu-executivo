"""add conversation ownership (posse por agente) to omnichannel threads

Revision ID: 44cc21307259
Revises: 0c5a0c7328ab
Create Date: 2026-07-25 20:30:00.000000

Harmonização multi-área no mesmo WhatsApp: quatro colunas novas em
``klm_omnichannel_threads`` para a posse forte (CAS) de conversa por agente,
distintas de ``owner_agent_key`` (rótulo informativo já existente, apenas
roteamento). Geridas exclusivamente por
``app.services.conversation_ownership_service``. Nenhuma tabela nova — a RLS
já vigente em ``klm_omnichannel_threads`` (migração
``7a8b9c0d1e2f_add_klm_hub_operational_core``) cobre as colunas novas sem
qualquer alteração de política.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "44cc21307259"
down_revision: str | None = "0c5a0c7328ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "klm_omnichannel_threads",
        sa.Column("owning_agent_key", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "klm_omnichannel_threads",
        sa.Column("owning_area_key", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "klm_omnichannel_threads",
        sa.Column("owner_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "klm_omnichannel_threads",
        sa.Column(
            "owner_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_index(
        "ix_klm_omnichannel_threads_owning_agent_key",
        "klm_omnichannel_threads",
        ["owning_agent_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_klm_omnichannel_threads_owning_agent_key",
        table_name="klm_omnichannel_threads",
    )
    op.drop_column("klm_omnichannel_threads", "owner_generation")
    op.drop_column("klm_omnichannel_threads", "owner_since")
    op.drop_column("klm_omnichannel_threads", "owning_area_key")
    op.drop_column("klm_omnichannel_threads", "owning_agent_key")
