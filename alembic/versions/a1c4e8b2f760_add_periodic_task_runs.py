"""Add periodic_task_runs (estado por task do Celery Beat).

Dormente #9 (docs/AUDITORIA_FINAL_UX_DORMENTES_2026-07.md): o beat tem
~28 tasks periódicas e a única superfície era o `GET /status` agregado,
que só conta workers vivos. Uma task específica podia falhar em silêncio
para sempre. Esta tabela guarda a ÚLTIMA execução por `task_name`.

Tabela de infraestrutura GLOBAL, sem `workspace_id`: o beat_schedule é do
deploy, não de um tenant. Mesmo enquadramento de
`system_status_observations` (99d0e1f2a3b4) — logo fora do inventário RLS
de `app/db/rls.py`, sem alterar a contagem literal de tabelas tenant.

Revision ID: a1c4e8b2f760
Revises: f3a7c92e5d81
Create Date: 2026-07-27 15:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "a1c4e8b2f760"
down_revision: str | Sequence[str] | None = "f3a7c92e5d81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "periodic_task_runs",
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_duration_ms", sa.Integer(), nullable=True),
        sa.Column("last_error_type", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("task_name", name="uq_periodic_task_runs_task_name"),
    )
    op.create_index(
        "ix_periodic_task_runs_task_name",
        "periodic_task_runs",
        ["task_name"],
        unique=True,
    )
    op.create_index(
        "ix_periodic_task_runs_last_status", "periodic_task_runs", ["last_status"]
    )


def downgrade() -> None:
    op.drop_index("ix_periodic_task_runs_last_status", table_name="periodic_task_runs")
    op.drop_index("ix_periodic_task_runs_task_name", table_name="periodic_task_runs")
    op.drop_table("periodic_task_runs")
