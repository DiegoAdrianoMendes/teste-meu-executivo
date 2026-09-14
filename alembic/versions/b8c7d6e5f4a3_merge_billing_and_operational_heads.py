"""merge billing and operational whatsapp heads

Revision ID: b8c7d6e5f4a3
Revises: a1b2c3d4e5f6, f1a2b3c4d5e6
Create Date: 2026-04-06 10:45:00.000000
"""

from typing import Sequence, Union


revision: str = "b8c7d6e5f4a3"
down_revision: Union[str, Sequence[str], None] = ("a1b2c3d4e5f6", "f1a2b3c4d5e6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
