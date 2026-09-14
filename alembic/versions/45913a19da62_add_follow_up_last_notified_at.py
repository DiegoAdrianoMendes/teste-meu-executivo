"""add last_notified_at to klm_follow_up_tasks (Onda 1 item 2 — disparo proativo)

Fecha o elo "Fila de follow-up sem disparo automático" do backlog de
dormentes (docs/ONDA1_DORMENTES_BACKLOG.md item 2): sem esta coluna não há
como o Celery Beat marcar, de forma idempotente, que um follow-up vencido já
foi notificado neste ciclo — o mesmo papel que `meetings.reminder_sent_at`
cumpre para `send-meeting-reminders-every-5-minutes`.

Revision ID: 45913a19da62
Revises: c7e9a1b3d526
Create Date: 2026-07-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "45913a19da62"
down_revision: str | Sequence[str] | None = "c7e9a1b3d526"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "klm_follow_up_tasks",
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_klm_follow_up_tasks_last_notified_at",
        "klm_follow_up_tasks",
        ["last_notified_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_klm_follow_up_tasks_last_notified_at", table_name="klm_follow_up_tasks")
    op.drop_column("klm_follow_up_tasks", "last_notified_at")
