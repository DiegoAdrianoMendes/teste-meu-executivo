"""Add the typed execution role package to agent deployments."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "lm4g7b0c9d53"
down_revision = "kl3f6a9b8c42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_deployments",
        sa.Column(
            "role_package",
            sa.String(length=80),
            nullable=False,
            server_default="legal_operations",
        ),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE agent_deployments
                SET role_package = COALESCE(
                    NULLIF(config->>'rolePackage', ''),
                    'legal_operations'
                ),
                    config = jsonb_set(
                        COALESCE(config, '{}'::jsonb),
                        '{rolePackage}',
                        to_jsonb(COALESCE(NULLIF(config->>'rolePackage', ''), 'legal_operations')),
                        true
                    )
                """
            )
        )
    op.alter_column("agent_deployments", "role_package", server_default=None)


def downgrade() -> None:
    op.drop_column("agent_deployments", "role_package")
