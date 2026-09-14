"""merge remaining alembic heads

Revision ID: c7b6a5d4e3f2
Revises: b8c7d6e5f4a3, b2c3d4e5f6a7, d9e0f1a2b3c4
Create Date: 2026-04-06 10:55:00.000000
"""

from typing import Sequence, Union


revision: str = "c7b6a5d4e3f2"
down_revision: Union[str, Sequence[str], None] = (
    "b8c7d6e5f4a3",
    "b2c3d4e5f6a7",
    "d9e0f1a2b3c4",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
