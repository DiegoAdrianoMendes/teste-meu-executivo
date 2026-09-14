"""merge password reset and public site heads

Revision ID: d9e0f1a2b3c4
Revises: a7b8c9d0e1f2, c9d8e7f6a5b4
Create Date: 2026-04-05 20:15:00.000000
"""

from typing import Sequence, Union


revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = ("a7b8c9d0e1f2", "c9d8e7f6a5b4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
