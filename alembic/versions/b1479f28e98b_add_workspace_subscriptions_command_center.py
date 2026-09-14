"""Add workspace_subscriptions and subscription_events (Sprint B1 Centro de Comando).

Aditiva. Cria a camada de assinatura comercial local-first (planos
essencial/escritorio/performance/custom, status trial/ativa/inadimplente/
pausada/cancelada) e o log de auditoria subscription_events. Faz backfill de
uma assinatura default para cada workspace existente, olhando primeiro a
Subscription/Plan Stripe-mirror já existente (se houver) e caindo para trial
essencial 14 dias quando não houver nada.

Revision ID: b1479f28e98b
Revises: b5d7f9a1c326
Create Date: 2026-07-23 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "b1479f28e98b"
down_revision: str | Sequence[str] | None = "b5d7f9a1c326"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "workspace_subscriptions",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plano", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="trial"),
        sa.Column("valor_mensal_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ciclo", sa.String(length=20), nullable=False, server_default="mensal"),
        sa.Column("desconto_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("cortesia", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("origem", sa.String(length=40), nullable=False, server_default="backfill"),
        sa.Column(
            "metadata_json",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", name="uq_workspace_subscriptions_workspace"),
        sa.CheckConstraint(
            "plano IN ('essencial', 'escritorio', 'performance', 'custom')",
            name="ck_workspace_subscriptions_plano",
        ),
        sa.CheckConstraint(
            "status IN ('trial', 'ativa', 'inadimplente', 'pausada', 'cancelada')",
            name="ck_workspace_subscriptions_status",
        ),
        sa.CheckConstraint("valor_mensal_cents >= 0", name="ck_workspace_subscriptions_valor_nonneg"),
    )
    op.create_index(
        "ix_workspace_subscriptions_status", "workspace_subscriptions", ["status"]
    )
    op.create_index(
        "ix_workspace_subscriptions_plano", "workspace_subscriptions", ["plano"]
    )

    op.create_table(
        "subscription_events",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column(
            "subscription_id",
            FlexibleUUID(),
            sa.ForeignKey("workspace_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column(
            "actor_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_label", sa.String(length=120), nullable=False, server_default="sistema"),
        sa.Column("motivo", sa.Text(), nullable=False, server_default=""),
        sa.Column("before_json", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("after_json", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_subscription_events_subscription_occurred",
        "subscription_events",
        ["subscription_id", "occurred_at"],
    )
    op.create_index("ix_subscription_events_workspace", "subscription_events", ["workspace_id"])

    # ── Backfill: uma assinatura default por workspace existente ──────────
    connection = op.get_bind()
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, autoload_with=connection)
    workspace_subscriptions = sa.Table(
        "workspace_subscriptions", metadata, autoload_with=connection
    )

    # Mapa best-effort do Subscription/Plan Stripe-mirror -> plano comercial novo.
    tier_to_plano = {
        "free": "essencial",
        "starter": "essencial",
        "pro": "escritorio",
        "business": "performance",
        "enterprise": "custom",
    }
    stripe_status_to_local = {
        "trialing": "trial",
        "active": "ativa",
        "past_due": "inadimplente",
        "cancelled": "cancelada",
        "unpaid": "inadimplente",
        "paused": "pausada",
    }
    plano_price_cents = {
        "essencial": 19700,
        "escritorio": 49700,
        "performance": 99700,
        "custom": 0,
    }

    existing_stripe_rows: dict = {}
    try:
        subscriptions = sa.Table("subscriptions", metadata, autoload_with=connection)
        plans = sa.Table("plans", metadata, autoload_with=connection)
        rows = connection.execute(
            sa.select(
                subscriptions.c.workspace_id,
                subscriptions.c.status,
                subscriptions.c.amount_cents,
                subscriptions.c.interval,
                subscriptions.c.trial_end,
                subscriptions.c.current_period_start,
                subscriptions.c.current_period_end,
                plans.c.tier,
            ).select_from(
                subscriptions.join(plans, plans.c.id == subscriptions.c.plan_id)
            )
        ).mappings().all()
        for row in rows:
            existing_stripe_rows[row["workspace_id"]] = row
    except Exception:
        existing_stripe_rows = {}

    now = datetime.now(timezone.utc)
    trial_default_end = now + timedelta(days=14)

    ws_rows = connection.execute(sa.select(workspaces.c.id)).all()
    to_insert = []
    for (workspace_id,) in ws_rows:
        stripe_row = existing_stripe_rows.get(workspace_id)
        if stripe_row is not None:
            tier_value = getattr(stripe_row["tier"], "value", stripe_row["tier"])
            status_value = getattr(stripe_row["status"], "value", stripe_row["status"])
            plano = tier_to_plano.get(str(tier_value), "essencial")
            status = stripe_status_to_local.get(str(status_value), "trial")
            valor = int(stripe_row["amount_cents"] or plano_price_cents[plano])
            interval_value = getattr(stripe_row["interval"], "value", stripe_row["interval"])
            ciclo = "anual" if str(interval_value) == "annually" else "mensal"
            origem = "backfill_stripe_mirror"
            trial_ends_at = stripe_row["trial_end"]
            period_start = stripe_row["current_period_start"]
            period_end = stripe_row["current_period_end"]
        else:
            plano = "essencial"
            status = "trial"
            valor = plano_price_cents["essencial"]
            ciclo = "mensal"
            origem = "backfill_default_trial"
            trial_ends_at = trial_default_end
            period_start = None
            period_end = None

        to_insert.append(
            {
                "workspace_id": workspace_id,
                "plano": plano,
                "status": status,
                "valor_mensal_cents": valor,
                "ciclo": ciclo,
                "desconto_pct": 0,
                "cortesia": False,
                "trial_ends_at": trial_ends_at,
                "current_period_start": period_start,
                "current_period_end": period_end,
                "cancelled_at": None,
                "origem": origem,
                "metadata_json": "{}" if is_sqlite else {},
            }
        )

    if to_insert:
        import uuid as uuid_module

        for row in to_insert:
            row["id"] = uuid_module.uuid4() if not is_sqlite else str(uuid_module.uuid4())
            if is_sqlite:
                row["metadata_json"] = "{}"
        connection.execute(sa.insert(workspace_subscriptions), to_insert)

        events_table = sa.Table("subscription_events", metadata, autoload_with=connection)
        subs_now = connection.execute(
            sa.select(workspace_subscriptions.c.id, workspace_subscriptions.c.workspace_id)
        ).all()
        event_rows = [
            {
                "id": str(uuid_module.uuid4()) if is_sqlite else uuid_module.uuid4(),
                "subscription_id": sub_id,
                "workspace_id": ws_id,
                "event_type": "backfill",
                "actor_user_id": None,
                "actor_label": "migration:b1479f28e98b",
                "motivo": "Backfill inicial da assinatura ao introduzir o Centro de Comando (B1).",
                "before_json": "{}" if is_sqlite else {},
                "after_json": "{}" if is_sqlite else {},
            }
            for sub_id, ws_id in subs_now
        ]
        if event_rows:
            connection.execute(sa.insert(events_table), event_rows)


def downgrade() -> None:
    op.drop_index("ix_subscription_events_workspace", table_name="subscription_events")
    op.drop_index(
        "ix_subscription_events_subscription_occurred", table_name="subscription_events"
    )
    op.drop_table("subscription_events")

    op.drop_index("ix_workspace_subscriptions_plano", table_name="workspace_subscriptions")
    op.drop_index("ix_workspace_subscriptions_status", table_name="workspace_subscriptions")
    op.drop_table("workspace_subscriptions")
