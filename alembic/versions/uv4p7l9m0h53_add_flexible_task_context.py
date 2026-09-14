"""Allow canonical tasks to use workspace, client, or Matter contexts.

The migration is deliberately expand/contract:

* existing tasks remain Matter-scoped and are backfilled without inventing a
  relationship;
* tasks gain a direct workspace anchor so RLS no longer depends on Matter;
* collaboration rows keep their historical ``matter_id`` snapshot, now
  nullable, and gain a direct ``(workspace_id, task_id)`` database binding;
* ``contract`` is part of the stable context vocabulary and is bound by the
  subsequent canonical client-contract migration.

Revision ID: uv4p7l9m0h53
Revises: tv3o6k8l9g42
Create Date: 2026-08-12 09:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "uv4p7l9m0h53"
down_revision: str | Sequence[str] | None = "tv3o6k8l9g42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


COLLABORATION_TABLES = (
    "task_participants",
    "task_review_rounds",
    "task_events",
    "task_checklists",
    "task_label_assignments",
    "task_attachments",
    "task_mentions",
)


def _direct_policy() -> str:
    return (
        "workspace_id::text = "
        "NULLIF(current_setting('app.current_workspace_id', true), '')"
    )


def _parent_policy() -> str:
    return (
        "EXISTS (SELECT 1 FROM matters WHERE "
        "matters.id = tasks.matter_id AND "
        "matters.workspace_id::text = "
        "NULLIF(current_setting('app.current_workspace_id', true), ''))"
    )


def upgrade() -> None:
    uuid_type = FlexibleUUID()
    op.add_column("tasks", sa.Column("workspace_id", uuid_type, nullable=True))
    op.add_column(
        "tasks",
        sa.Column(
            "context_type",
            sa.String(length=24),
            server_default=sa.text("'matter'"),
            nullable=False,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "origin",
            sa.String(length=32),
            server_default=sa.text("'legacy'"),
            nullable=False,
        ),
    )
    op.add_column("tasks", sa.Column("client_id", uuid_type, nullable=True))
    op.add_column("tasks", sa.Column("contract_id", uuid_type, nullable=True))
    op.add_column("tasks", sa.Column("origin_resource_id", uuid_type, nullable=True))
    op.add_column("tasks", sa.Column("origin_label", sa.String(length=240), nullable=True))

    # ``tasks`` and its source table ``matters`` already have FORCE RLS while
    # the AWS migrator intentionally has NOBYPASSRLS. Both policies must be
    # disabled inside this migration transaction: disabling only ``tasks``
    # still makes the scalar subquery below see zero Matter rows. PostgreSQL
    # keeps these DDL changes transactional, and both policies are restored
    # before the transaction can commit.
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "tasks" DISABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "matters" DISABLE ROW LEVEL SECURITY')
    op.execute(
        sa.text(
            "UPDATE tasks SET workspace_id = "
            "(SELECT matters.workspace_id FROM matters WHERE matters.id = tasks.matter_id)"
        )
    )
    remaining = op.get_bind().execute(
        sa.text("SELECT count(*) FROM tasks WHERE workspace_id IS NULL")
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            "Cannot backfill tasks.workspace_id: tasks without a valid Matter remain."
        )
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.alter_column("workspace_id", existing_type=uuid_type, nullable=False)
        batch_op.alter_column("matter_id", existing_type=uuid_type, nullable=True)
        batch_op.create_unique_constraint("uq_tasks_workspace_id", ["workspace_id", "id"])
        batch_op.create_foreign_key(
            "fk_tasks_workspace",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_tasks_workspace_client",
            "clients",
            ["workspace_id", "client_id"],
            ["workspace_id", "id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_tasks_parent_same_workspace",
            "tasks",
            ["workspace_id", "parent_task_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_check_constraint(
            "ck_tasks_context_coherent",
            "(context_type = 'workspace' AND matter_id IS NULL AND client_id IS NULL "
            "AND contract_id IS NULL) OR "
            "(context_type = 'client' AND matter_id IS NULL AND client_id IS NOT NULL "
            "AND contract_id IS NULL) OR "
            "(context_type = 'matter' AND matter_id IS NOT NULL AND client_id IS NULL "
            "AND contract_id IS NULL) OR "
            "(context_type = 'contract' AND matter_id IS NULL AND client_id IS NOT NULL "
            "AND contract_id IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_tasks_legal_origin_requires_matter",
            "origin NOT IN ('publication', 'process', 'hearing', 'deadline') "
            "OR context_type = 'matter'",
        )
    op.create_index("ix_tasks_workspace_id", "tasks", ["workspace_id"])
    op.create_index("ix_tasks_client_id", "tasks", ["client_id"])
    op.create_index("ix_tasks_contract_id", "tasks", ["contract_id"])
    op.create_index("ix_tasks_context_type", "tasks", ["context_type"])
    op.create_index("ix_tasks_origin", "tasks", ["origin"])
    op.create_index("ix_tasks_origin_resource_id", "tasks", ["origin_resource_id"])
    op.create_index(
        "ux_tasks_workspace_idempotency",
        "tasks",
        ["workspace_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )

    for table_name in COLLABORATION_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column("matter_id", existing_type=uuid_type, nullable=True)
            batch_op.create_foreign_key(
                f"fk_{table_name}_workspace_task_context",
                "tasks",
                ["workspace_id", "task_id"],
                ["workspace_id", "id"],
                ondelete="CASCADE" if table_name != "task_events" else "RESTRICT",
            )

    with op.batch_alter_table("task_dependencies") as batch_op:
        batch_op.alter_column("matter_id", existing_type=uuid_type, nullable=True)
        batch_op.create_foreign_key(
            "fk_task_dependencies_workspace_predecessor_context",
            "tasks",
            ["workspace_id", "predecessor_task_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_task_dependencies_workspace_successor_context",
            "tasks",
            ["workspace_id", "successor_task_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS workspace_current_id ON tasks")
        op.execute("DROP POLICY IF EXISTS klm_workspace_isolation ON tasks")
        predicate = _direct_policy()
        op.execute(
            f"CREATE POLICY workspace_current_id ON tasks "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
        op.execute("ALTER TABLE tasks ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE tasks FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE matters ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE matters FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    bind = op.get_bind()
    orphan_count = bind.execute(
        sa.text("SELECT count(*) FROM tasks WHERE matter_id IS NULL")
    ).scalar_one()
    if orphan_count:
        raise RuntimeError(
            "Cannot downgrade flexible task context while non-Matter tasks exist. "
            "Reclassify or archive them explicitly first."
        )

    uuid_type = FlexibleUUID()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS workspace_current_id ON tasks")
        predicate = _parent_policy()
        op.execute(
            f"CREATE POLICY workspace_current_id ON tasks "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )

    with op.batch_alter_table("task_dependencies") as batch_op:
        batch_op.drop_constraint(
            "fk_task_dependencies_workspace_successor_context", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_task_dependencies_workspace_predecessor_context", type_="foreignkey"
        )
        batch_op.alter_column("matter_id", existing_type=uuid_type, nullable=False)

    for table_name in reversed(COLLABORATION_TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(
                f"fk_{table_name}_workspace_task_context", type_="foreignkey"
            )
            batch_op.alter_column("matter_id", existing_type=uuid_type, nullable=False)

    op.drop_index("ux_tasks_workspace_idempotency", table_name="tasks")
    for index_name in (
        "ix_tasks_origin",
        "ix_tasks_origin_resource_id",
        "ix_tasks_context_type",
        "ix_tasks_contract_id",
        "ix_tasks_client_id",
        "ix_tasks_workspace_id",
    ):
        op.drop_index(index_name, table_name="tasks")
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_constraint("ck_tasks_legal_origin_requires_matter", type_="check")
        batch_op.drop_constraint("ck_tasks_context_coherent", type_="check")
        batch_op.drop_constraint("fk_tasks_parent_same_workspace", type_="foreignkey")
        batch_op.drop_constraint("fk_tasks_workspace_client", type_="foreignkey")
        batch_op.drop_constraint("fk_tasks_workspace", type_="foreignkey")
        batch_op.drop_constraint("uq_tasks_workspace_id", type_="unique")
        batch_op.alter_column("matter_id", existing_type=uuid_type, nullable=False)
    op.drop_column("tasks", "contract_id")
    op.drop_column("tasks", "client_id")
    op.drop_column("tasks", "origin_label")
    op.drop_column("tasks", "origin_resource_id")
    op.drop_column("tasks", "origin")
    op.drop_column("tasks", "context_type")
    op.drop_column("tasks", "workspace_id")
