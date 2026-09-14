"""Add growth_prospects, growth_activities, growth_stage_events (Growth CRM da plataforma).

Aditiva. Cria o funil de aquisição B2B dos escritórios-clientes usado pelo
Super Admin (staff-only) para captar e gerir prospects — algo que o KLM Hub
até então não tinha para si mesmo, apesar de vender CRM. Estágios:
prospect -> contatado -> demo_agendada -> trial -> proposta -> pagante | perdido.

Faz backfill: cada workspace existente com WorkspaceSubscription vira um
GrowthProspect já no estágio "pagante" (status ativa/inadimplente) ou
"trial" (status trial), ligando o CRM à base real desde o dia 1.

Revision ID: 8af3d982fb78
Revises: c2f8a1d4e6b9
Create Date: 2026-07-23 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "8af3d982fb78"
down_revision: str | Sequence[str] | None = "c2f8a1d4e6b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FUNNEL_STAGES = (
    "prospect",
    "contatado",
    "demo_agendada",
    "trial",
    "proposta",
    "pagante",
    "perdido",
)
_ACQUISITION_CHANNELS = (
    "indicacao",
    "outbound",
    "inbound_site",
    "evento",
    "rede_propria",
    "parceria",
    "anuncio",
    "backfill",
    "outro",
)
_ACTIVITY_TYPES = ("ligacao", "email", "whatsapp", "demo", "nota")


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "growth_prospects",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("nome_escritorio", sa.String(length=200), nullable=False),
        sa.Column("contato_nome", sa.String(length=150), nullable=True),
        sa.Column("contato_email", sa.String(length=255), nullable=True),
        sa.Column("contato_telefone", sa.String(length=40), nullable=True),
        sa.Column("cidade", sa.String(length=120), nullable=True),
        sa.Column("uf", sa.String(length=2), nullable=True),
        sa.Column("tamanho_estimado", sa.String(length=40), nullable=True),
        sa.Column("origem", sa.String(length=20), nullable=False, server_default="outro"),
        sa.Column("estagio", sa.String(length=20), nullable=False, server_default="prospect"),
        sa.Column("valor_estimado_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("motivo_perda", sa.Text(), nullable=True),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column(
            "owner_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "stage_entered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("won_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lost_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata_json", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "estagio IN (" + ", ".join(repr(s) for s in _FUNNEL_STAGES) + ")",
            name="ck_growth_prospects_estagio",
        ),
        sa.CheckConstraint(
            "origem IN (" + ", ".join(repr(c) for c in _ACQUISITION_CHANNELS) + ")",
            name="ck_growth_prospects_origem",
        ),
        sa.CheckConstraint("valor_estimado_cents >= 0", name="ck_growth_prospects_valor_nonneg"),
    )
    op.create_index("ix_growth_prospects_estagio", "growth_prospects", ["estagio"])
    op.create_index("ix_growth_prospects_origem", "growth_prospects", ["origem"])
    op.create_index("ix_growth_prospects_owner", "growth_prospects", ["owner_user_id"])
    op.create_index("ix_growth_prospects_workspace", "growth_prospects", ["workspace_id"])

    op.create_table(
        "growth_activities",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column(
            "prospect_id",
            FlexibleUUID(),
            sa.ForeignKey("growth_prospects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "actor_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_label", sa.String(length=120), nullable=False, server_default="sistema"),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "tipo IN (" + ", ".join(repr(t) for t in _ACTIVITY_TYPES) + ")",
            name="ck_growth_activities_tipo",
        ),
    )
    op.create_index(
        "ix_growth_activities_prospect_occurred",
        "growth_activities",
        ["prospect_id", "occurred_at"],
    )

    op.create_table(
        "growth_stage_events",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column(
            "prospect_id",
            FlexibleUUID(),
            sa.ForeignKey("growth_prospects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_estagio", sa.String(length=20), nullable=True),
        sa.Column("to_estagio", sa.String(length=20), nullable=False),
        sa.Column(
            "actor_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_label", sa.String(length=120), nullable=False, server_default="sistema"),
        sa.Column("motivo", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "from_estagio IS NULL OR from_estagio IN ("
            + ", ".join(repr(s) for s in _FUNNEL_STAGES)
            + ")",
            name="ck_growth_stage_events_from",
        ),
        sa.CheckConstraint(
            "to_estagio IN (" + ", ".join(repr(s) for s in _FUNNEL_STAGES) + ")",
            name="ck_growth_stage_events_to",
        ),
    )
    op.create_index(
        "ix_growth_stage_events_prospect_occurred",
        "growth_stage_events",
        ["prospect_id", "occurred_at"],
    )

    # ── Backfill: cada workspace com assinatura vira um prospect ganho/trial ──
    connection = op.get_bind()
    metadata = sa.MetaData()
    try:
        workspaces = sa.Table("workspaces", metadata, autoload_with=connection)
        workspace_subscriptions = sa.Table(
            "workspace_subscriptions", metadata, autoload_with=connection
        )
    except Exception:
        return

    growth_prospects = sa.Table("growth_prospects", metadata, autoload_with=connection)

    status_to_stage = {
        "trial": "trial",
        "ativa": "pagante",
        "inadimplente": "pagante",
        "pausada": "pagante",
        "cancelada": "perdido",
    }

    name_col = None
    for candidate in ("name", "nome", "razao_social", "display_name"):
        if candidate in workspaces.c:
            name_col = workspaces.c[candidate]
            break

    select_cols = [
        workspaces.c.id.label("workspace_id"),
        workspace_subscriptions.c.status,
        workspace_subscriptions.c.valor_mensal_cents,
        workspace_subscriptions.c.created_at,
    ]
    if name_col is not None:
        select_cols.append(name_col.label("nome_escritorio"))

    rows = connection.execute(
        sa.select(*select_cols).select_from(
            workspaces.join(
                workspace_subscriptions,
                workspace_subscriptions.c.workspace_id == workspaces.c.id,
            )
        )
    ).mappings().all()

    if not rows:
        return

    import uuid as uuid_module

    now = datetime.now(timezone.utc)
    prospect_rows = []
    for row in rows:
        status_value = getattr(row["status"], "value", row["status"])
        estagio = status_to_stage.get(str(status_value), "trial")
        nome = row.get("nome_escritorio") or f"Workspace {row['workspace_id']}"
        prospect_id = uuid_module.uuid4()
        prospect_rows.append(
            {
                "id": str(prospect_id) if is_sqlite else prospect_id,
                "nome_escritorio": str(nome)[:200],
                "contato_nome": None,
                "contato_email": None,
                "contato_telefone": None,
                "cidade": None,
                "uf": None,
                "tamanho_estimado": None,
                "origem": "backfill",
                "estagio": estagio,
                "valor_estimado_cents": int(row["valor_mensal_cents"] or 0),
                "motivo_perda": None,
                "notas": "Backfill automático a partir da assinatura existente do workspace.",
                "owner_user_id": None,
                "workspace_id": str(row["workspace_id"]) if is_sqlite else row["workspace_id"],
                "stage_entered_at": row["created_at"] or now,
                "won_at": now if estagio == "pagante" else None,
                "lost_at": now if estagio == "perdido" else None,
                "metadata_json": "{}" if is_sqlite else {},
            }
        )

    connection.execute(sa.insert(growth_prospects), prospect_rows)

    stage_events_table = sa.Table("growth_stage_events", metadata, autoload_with=connection)
    event_rows = [
        {
            "id": str(uuid_module.uuid4()) if is_sqlite else uuid_module.uuid4(),
            "prospect_id": row["id"],
            "from_estagio": None,
            "to_estagio": row["estagio"],
            "actor_user_id": None,
            "actor_label": "migration:8af3d982fb78",
            "motivo": "Backfill inicial do Growth CRM a partir da assinatura existente.",
        }
        for row in prospect_rows
    ]
    if event_rows:
        connection.execute(sa.insert(stage_events_table), event_rows)


def downgrade() -> None:
    op.drop_index("ix_growth_stage_events_prospect_occurred", table_name="growth_stage_events")
    op.drop_table("growth_stage_events")

    op.drop_index("ix_growth_activities_prospect_occurred", table_name="growth_activities")
    op.drop_table("growth_activities")

    op.drop_index("ix_growth_prospects_workspace", table_name="growth_prospects")
    op.drop_index("ix_growth_prospects_owner", table_name="growth_prospects")
    op.drop_index("ix_growth_prospects_origem", table_name="growth_prospects")
    op.drop_index("ix_growth_prospects_estagio", table_name="growth_prospects")
    op.drop_table("growth_prospects")
