"""persist required password change state

Revision ID: op7i0e3g2b86
Revises: no6h9d2f1a75
Create Date: 2026-08-05 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "op7i0e3g2b86"
down_revision: str | Sequence[str] | None = "no6h9d2f1a75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_users_required_password_change_has_temporary_password"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "password_change_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column("temporary_password_issued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        CONSTRAINT,
        "users",
        "NOT password_change_required OR "
        "(hashed_password IS NOT NULL AND temporary_password_issued_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "users", type_="check")
    op.drop_column("users", "temporary_password_issued_at")
    op.drop_column("users", "password_change_required")
