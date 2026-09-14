"""Enforce one KLM workspace per local ChatterMate organization.

Revision ID: 96a7b8c9d0e1
Revises: 95f6a7b8c9d0
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "96a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "95f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_BINDING = sa.text(
    "provider = 'chattermate' AND klm_entity_type = 'workspace' "
    "AND external_entity_type = 'organization' AND sync_state = 'synced'"
)


def upgrade() -> None:
    op.create_index(
        "uq_oss_chattermate_active_organization_binding",
        "oss_external_entity_links",
        ["provider", "external_account_id"],
        unique=True,
        postgresql_where=_ACTIVE_BINDING,
        sqlite_where=_ACTIVE_BINDING,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_oss_chattermate_active_organization_binding",
        table_name="oss_external_entity_links",
    )
