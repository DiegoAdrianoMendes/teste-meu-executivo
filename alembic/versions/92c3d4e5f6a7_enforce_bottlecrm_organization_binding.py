"""Enforce one KLM workspace per local BottleCRM organization.

Revision ID: 92c3d4e5f6a7
Revises: 91b2c3d4e5f6
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "92c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "91b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_BINDING = sa.text(
    "provider = 'bottlecrm' AND klm_entity_type = 'workspace' "
    "AND external_entity_type = 'organization' AND sync_state = 'synced'"
)


def upgrade() -> None:
    op.create_index(
        "uq_oss_bottlecrm_active_organization_binding",
        "oss_external_entity_links",
        ["provider", "external_account_id"],
        unique=True,
        postgresql_where=_ACTIVE_BINDING,
        sqlite_where=_ACTIVE_BINDING,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_oss_bottlecrm_active_organization_binding",
        table_name="oss_external_entity_links",
    )
