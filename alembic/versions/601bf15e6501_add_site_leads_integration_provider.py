"""add site_leads integration provider (F12 webhook de leads de sites)

Revision ID: 601bf15e6501
Revises: f8a2d443a6ee
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "601bf15e6501"
down_revision: Union[str, None] = "f8a2d443a6ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE integration_provider_enum ADD VALUE IF NOT EXISTS 'site_leads'")


def downgrade() -> None:
    # PostgreSQL enums do not support DROP VALUE directly.
    # Downgrade is intentionally a no-op to avoid destructive enum recreation.
    pass
