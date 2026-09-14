"""Enforce one active Chatwoot account binding per KLM workspace mapping.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ACTIVE_BINDING = sa.text(
    "provider = 'chatwoot' AND klm_entity_type = 'workspace' "
    "AND external_entity_type = 'account' AND sync_state = 'synced'"
)


def upgrade() -> None:
    op.create_index(
        "uq_oss_chatwoot_active_account_binding",
        "oss_external_entity_links",
        ["provider", "external_account_id"],
        unique=True,
        postgresql_where=_ACTIVE_BINDING,
        sqlite_where=_ACTIVE_BINDING,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_oss_chatwoot_active_account_binding",
        table_name="oss_external_entity_links",
    )
