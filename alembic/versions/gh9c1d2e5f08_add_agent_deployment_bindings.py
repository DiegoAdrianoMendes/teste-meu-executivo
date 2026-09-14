"""Persist typed AgentDeployment V2 bindings."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON

revision: str = "gh9c1d2e5f08"
down_revision: str | Sequence[str] | None = "fg8b0c1d4e97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in (
        "tool_bindings",
        "knowledge_bindings",
        "autonomy_policy",
        "human_routing_policy",
        "budget_policy",
    ):
        op.add_column(
            "agent_deployments",
            sa.Column(name, FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        )
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE agent_deployments
                SET tool_bindings = COALESCE(config->'tools', '[]'::jsonb),
                    knowledge_bindings = COALESCE(
                        config->'knowledgeSources', '[]'::jsonb
                    ),
                    autonomy_policy = jsonb_build_object(
                        'key', config->>'autonomyPolicy'
                    ),
                    human_routing_policy = jsonb_build_object(
                        'key', config->>'humanRoutingPolicy'
                    ),
                    budget_policy = COALESCE(
                        config->'budgetPolicy', '{}'::jsonb
                    )
                """
            )
        )
    for name in (
        "tool_bindings",
        "knowledge_bindings",
        "autonomy_policy",
        "human_routing_policy",
        "budget_policy",
    ):
        op.alter_column("agent_deployments", name, server_default=None)


def downgrade() -> None:
    for name in (
        "budget_policy",
        "human_routing_policy",
        "autonomy_policy",
        "knowledge_bindings",
        "tool_bindings",
    ):
        op.drop_column("agent_deployments", name)
