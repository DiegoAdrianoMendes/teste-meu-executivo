"""Add legal task lists, boards, columns and logical archive.

Revision ID: za8t1q4r5m97
Revises: yz7s0p4q5l86
"""

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision = "za8t1q4r5m97"
down_revision = "yz7s0p4q5l86"
branch_labels = None
depends_on = None


def _enable_rls(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = "workspace_id::text = NULLIF(current_setting('app.current_workspace_id', true), '')"
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "workspace_current_id" ON "{table}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def upgrade() -> None:
    uuid_type = FlexibleUUID()
    op.add_column("tasks", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("tasks", sa.Column("archived_by_user_id", uuid_type))
    op.add_column("tasks", sa.Column("archive_reason", sa.Text()))
    op.create_foreign_key(
        "fk_tasks_archived_by_user",
        "tasks",
        "users",
        ["archived_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_tasks_archived_at", "tasks", ["archived_at"])

    op.create_table(
        "task_lists",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", uuid_type),
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_task_lists_workspace_id"),
        sa.UniqueConstraint("workspace_id", "normalized_name", name="uq_task_lists_workspace_name"),
        sa.CheckConstraint("version >= 1", name="ck_task_lists_version_positive"),
    )
    op.create_index("ix_task_lists_workspace_id", "task_lists", ["workspace_id"])
    op.create_index("ix_task_lists_archived_at", "task_lists", ["archived_at"])

    op.create_table(
        "task_list_memberships",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("list_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", uuid_type),
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "list_id"],
            ["task_lists.workspace_id", "task_lists.id"],
            name="fk_task_list_memberships_workspace_list",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_list_memberships_workspace_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("list_id", "task_id", name="uq_task_list_membership"),
    )
    op.create_index(
        "ix_task_list_memberships_workspace_list",
        "task_list_memberships",
        ["workspace_id", "list_id"],
    )
    op.create_index(
        "ix_task_list_memberships_workspace_id",
        "task_list_memberships",
        ["workspace_id"],
    )
    op.create_index("ix_task_list_memberships_list_id", "task_list_memberships", ["list_id"])
    op.create_index("ix_task_list_memberships_task_id", "task_list_memberships", ["task_id"])

    op.create_table(
        "task_boards",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", uuid_type),
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_task_boards_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "normalized_name", name="uq_task_boards_workspace_name"
        ),
        sa.CheckConstraint("version >= 1", name="ck_task_boards_version_positive"),
    )
    op.create_index("ix_task_boards_workspace_id", "task_boards", ["workspace_id"])
    op.create_index("ix_task_boards_archived_at", "task_boards", ["archived_at"])

    op.create_table(
        "task_board_columns",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("board_id", uuid_type, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "board_id"],
            ["task_boards.workspace_id", "task_boards.id"],
            name="fk_task_board_columns_workspace_board",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_task_board_columns_workspace_id"),
        sa.UniqueConstraint("board_id", "normalized_name", name="uq_task_board_columns_board_name"),
        sa.UniqueConstraint("board_id", "position", name="uq_task_board_columns_board_position"),
        sa.CheckConstraint("version >= 1", name="ck_task_board_columns_version_positive"),
    )
    op.create_index("ix_task_board_columns_workspace_id", "task_board_columns", ["workspace_id"])
    op.create_index("ix_task_board_columns_board_id", "task_board_columns", ["board_id"])
    op.create_index("ix_task_board_columns_archived_at", "task_board_columns", ["archived_at"])

    op.create_table(
        "task_board_placements",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("board_id", uuid_type, nullable=False),
        sa.Column("column_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("moved_by_user_id", uuid_type),
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "board_id"],
            ["task_boards.workspace_id", "task_boards.id"],
            name="fk_task_board_placements_workspace_board",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "column_id"],
            ["task_board_columns.workspace_id", "task_board_columns.id"],
            name="fk_task_board_placements_workspace_column",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_board_placements_workspace_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["moved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("board_id", "task_id", name="uq_task_board_placement"),
        sa.CheckConstraint("version >= 1", name="ck_task_board_placements_version_positive"),
    )
    op.create_index(
        "ix_task_board_placements_workspace_column",
        "task_board_placements",
        ["workspace_id", "column_id"],
    )
    op.create_index(
        "ix_task_board_placements_workspace_id",
        "task_board_placements",
        ["workspace_id"],
    )
    op.create_index("ix_task_board_placements_board_id", "task_board_placements", ["board_id"])
    op.create_index(
        "ix_task_board_placements_column_id",
        "task_board_placements",
        ["column_id"],
    )
    op.create_index("ix_task_board_placements_task_id", "task_board_placements", ["task_id"])

    for table in (
        "task_lists",
        "task_list_memberships",
        "task_boards",
        "task_board_columns",
        "task_board_placements",
    ):
        _enable_rls(table)

    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "task_events" DROP CONSTRAINT IF EXISTS "task_event_type_enum"')
        op.execute(
            """ALTER TABLE "task_events" ADD CONSTRAINT "task_event_type_enum"
            CHECK (event_type IN (
              'created','updated','participants_changed','participant_state_changed',
              'started','blocked','unblocked','review_submitted','review_approved',
              'review_changes_requested','completed','reopened','cancelled','comment_added',
              'checklist_changed','labels_changed','dependency_changed','attachment_changed',
              'organization_changed','archived','unarchived'
            ))"""
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        forbidden = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT count(*) FROM task_events "
                    "WHERE event_type IN ('organization_changed','archived','unarchived')"
                )
            )
            .scalar_one()
        )
        if forbidden:
            raise RuntimeError("Downgrade bloqueado: existem eventos de organização/arquivo.")
        op.execute('ALTER TABLE "task_events" DROP CONSTRAINT IF EXISTS "task_event_type_enum"')
        op.execute(
            """ALTER TABLE "task_events" ADD CONSTRAINT "task_event_type_enum"
            CHECK (event_type IN (
              'created','updated','participants_changed','participant_state_changed',
              'started','blocked','unblocked','review_submitted','review_approved',
              'review_changes_requested','completed','reopened','cancelled','comment_added',
              'checklist_changed','labels_changed','dependency_changed','attachment_changed'
            ))"""
        )
    op.drop_table("task_board_placements")
    op.drop_table("task_board_columns")
    op.drop_table("task_boards")
    op.drop_table("task_list_memberships")
    op.drop_table("task_lists")
    op.drop_index("ix_tasks_archived_at", table_name="tasks")
    op.drop_constraint("fk_tasks_archived_by_user", "tasks", type_="foreignkey")
    op.drop_column("tasks", "archive_reason")
    op.drop_column("tasks", "archived_by_user_id")
    op.drop_column("tasks", "archived_at")
