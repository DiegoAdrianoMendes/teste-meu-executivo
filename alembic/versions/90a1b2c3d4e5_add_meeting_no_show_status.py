"""Add the manually recorded no-show meeting status.

Revision ID: 90a1b2c3d4e5
Revises: 8f92a3b4c5d6
"""

from __future__ import annotations

from alembic import op

revision = "90a1b2c3d4e5"
down_revision = "8f92a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE meeting_status_enum ADD VALUE IF NOT EXISTS 'no_show'")


def downgrade() -> None:
    # PostgreSQL cannot safely remove one enum value in place. Keeping the value
    # is preferable to rebuilding the type and risking persisted meeting rows.
    pass
