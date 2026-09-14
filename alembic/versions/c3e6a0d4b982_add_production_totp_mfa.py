"""add production TOTP MFA credentials

Revision ID: c3e6a0d4b982
Revises: b2d5f9c3a871
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3e6a0d4b982"
down_revision: str | Sequence[str] | None = "b2d5f9c3a871"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mfa_credentials", sa.Column("totp_secret_encrypted", sa.Text(), nullable=True))
    op.add_column("mfa_credentials", sa.Column("last_totp_counter", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("mfa_credentials", "last_totp_counter")
    op.drop_column("mfa_credentials", "totp_secret_encrypted")
