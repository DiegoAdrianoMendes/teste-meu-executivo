"""Alinha os timestamps do catálogo de medicamentos ao server_default do modelo.

A migration 94e5f6a7b8c9 criou created_at/updated_at NOT NULL sem server_default,
divergindo do TimestampMixin (que declara server_default=func.now()). No Postgres
isso quebra inserts do ORM que confiam no default do banco (NotNullViolation).
Esta migration adiciona o default às quatro colunas; é aditiva e reversível.

Revision ID: c2f8a1d4e6b9
Revises: b1479f28e98b
Create Date: 2026-07-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c2f8a1d4e6b9"
down_revision = "b1479f28e98b"
branch_labels = None
depends_on = None

_TABLES = ("medication_references", "matter_treatment_estimates")
_COLUMNS = ("created_at", "updated_at")


def upgrade() -> None:
    for table in _TABLES:
        for column in _COLUMNS:
            op.alter_column(
                table,
                column,
                server_default=sa.func.now(),
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
            )


def downgrade() -> None:
    for table in _TABLES:
        for column in _COLUMNS:
            op.alter_column(
                table,
                column,
                server_default=None,
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
            )
