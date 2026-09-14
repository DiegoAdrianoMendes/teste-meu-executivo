"""expand integration providers

Revision ID: f2a4b6c8d001
Revises: e19f2b7a4c21
Create Date: 2026-04-03 23:05:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f2a4b6c8d001"
down_revision: Union[str, None] = "e19f2b7a4c21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE integration_provider_enum ADD VALUE IF NOT EXISTS 'zapsign'")
    op.execute("ALTER TYPE integration_provider_enum ADD VALUE IF NOT EXISTS 'docusign'")


def downgrade() -> None:
    # PostgreSQL enums do not support DROP VALUE directly.
    # Downgrade is intentionally a no-op to avoid destructive enum recreation.
    pass
