"""Index acquisition events used by bounded agent context queries.

Revision ID: 6a8c0e2f4b76
Revises: 5f7b9d1e3a65
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6a8c0e2f4b76"
down_revision: str | None = "5f7b9d1e3a65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_klm_acquisition_workspace_matter_happened",
        "klm_acquisition_events",
        ["workspace_id", "matter_id", "happened_at"],
        unique=False,
        postgresql_where=sa.text("matter_id IS NOT NULL"),
    )
    op.create_index(
        "ix_klm_acquisition_workspace_client_unbound_happened",
        "klm_acquisition_events",
        ["workspace_id", "client_id", "happened_at"],
        unique=False,
        postgresql_where=sa.text("matter_id IS NULL AND client_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_klm_acquisition_workspace_client_unbound_happened",
        table_name="klm_acquisition_events",
    )
    op.drop_index(
        "ix_klm_acquisition_workspace_matter_happened",
        table_name="klm_acquisition_events",
    )
