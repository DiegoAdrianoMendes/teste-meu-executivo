# ruff: noqa: E501
"""Add structured legal task checklists.

Revision ID: tv3o6k8l9g42
Revises: su2n5j8k7f31
"""

import sqlalchemy as sa

from alembic import op

revision = "tv3o6k8l9g42"
down_revision = "su2n5j8k7f31"
branch_labels = None
depends_on = None


def _enable_rls(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        "workspace_id::text = "
        "NULLIF(current_setting('app.current_workspace_id', true), '')"
    )
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "workspace_current_id" ON "{table}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def upgrade() -> None:
    uuid_type = sa.Uuid()
    op.add_column("tasks", sa.Column("parent_task_id", uuid_type, nullable=True))
    op.create_index("ix_tasks_parent_task_id", "tasks", ["parent_task_id"])
    op.create_foreign_key(
        "fk_tasks_parent_same_matter",
        "tasks",
        "tasks",
        ["matter_id", "parent_task_id"],
        ["matter_id", "id"],
        ondelete="CASCADE",
    )
    op.create_table(
        "task_checklists",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_task_checklists_version_positive"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_checklists_matter_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_checklists_workspace_matter",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_task_checklists_workspace_id"),
    )
    op.create_index("ix_task_checklists_workspace_id", "task_checklists", ["workspace_id"])
    op.create_index("ix_task_checklists_matter_id", "task_checklists", ["matter_id"])
    op.create_index("ix_task_checklists_task_id", "task_checklists", ["task_id"])
    op.create_index("ix_task_checklists_archived_at", "task_checklists", ["archived_at"])
    op.create_index("ix_task_checklists_workspace_task", "task_checklists", ["workspace_id", "task_id"])

    op.create_table(
        "task_checklist_items",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("checklist_id", uuid_type, nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_user_id", uuid_type, nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_task_checklist_items_version_positive"),
        sa.ForeignKeyConstraint(["completed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "checklist_id"],
            ["task_checklists.workspace_id", "task_checklists.id"],
            name="fk_task_checklist_items_workspace_checklist",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_checklist_items_workspace_id", "task_checklist_items", ["workspace_id"])
    op.create_index("ix_task_checklist_items_checklist_id", "task_checklist_items", ["checklist_id"])
    op.create_index(
        "ix_task_checklist_items_workspace_checklist",
        "task_checklist_items",
        ["workspace_id", "checklist_id"],
    )

    op.create_table(
        "task_labels",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "normalized_name", name="uq_task_labels_workspace_name"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_task_labels_workspace_id"),
    )
    op.create_index("ix_task_labels_workspace_id", "task_labels", ["workspace_id"])

    op.create_table(
        "task_label_assignments",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("label_id", uuid_type, nullable=False),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["matter_id", "task_id"], ["tasks.matter_id", "tasks.id"],
            name="fk_task_label_assignments_matter_task", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "label_id"], ["task_labels.workspace_id", "task_labels.id"],
            name="fk_task_label_assignments_workspace_label", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "label_id", name="uq_task_label_assignment"),
    )
    op.create_index("ix_task_label_assignments_workspace_id", "task_label_assignments", ["workspace_id"])
    op.create_index("ix_task_label_assignments_matter_id", "task_label_assignments", ["matter_id"])
    op.create_index("ix_task_label_assignments_task_id", "task_label_assignments", ["task_id"])
    op.create_index("ix_task_label_assignments_label_id", "task_label_assignments", ["label_id"])
    op.create_index("ix_task_label_assignments_workspace_task", "task_label_assignments", ["workspace_id", "task_id"])

    op.create_table(
        "task_dependencies",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("predecessor_task_id", uuid_type, nullable=False),
        sa.Column("successor_task_id", uuid_type, nullable=False),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("predecessor_task_id <> successor_task_id", name="ck_task_dependency_distinct"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["matter_id", "predecessor_task_id"], ["tasks.matter_id", "tasks.id"],
            name="fk_task_dependencies_predecessor", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["matter_id", "successor_task_id"], ["tasks.matter_id", "tasks.id"],
            name="fk_task_dependencies_successor", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("predecessor_task_id", "successor_task_id", name="uq_task_dependency_edge"),
    )
    for column in ("workspace_id", "matter_id", "predecessor_task_id", "successor_task_id"):
        op.create_index(f"ix_task_dependencies_{column}", "task_dependencies", [column])
    op.create_index("ix_task_dependencies_workspace_matter", "task_dependencies", ["workspace_id", "matter_id"])

    op.create_table(
        "task_attachments",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("task_event_id", uuid_type, nullable=False),
        sa.Column("document_id", uuid_type, nullable=False),
        sa.Column("name_snapshot", sa.String(length=500), nullable=False),
        sa.Column("mime_type_snapshot", sa.String(length=100), nullable=True),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["matter_id", "task_id"], ["tasks.matter_id", "tasks.id"],
            name="fk_task_attachments_matter_task", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["task_event_id"], ["task_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_event_id", "document_id", name="uq_task_event_document"),
    )
    for column in ("workspace_id", "matter_id", "task_id", "task_event_id", "document_id"):
        op.create_index(f"ix_task_attachments_{column}", "task_attachments", [column])
    op.create_index("ix_task_attachments_workspace_task", "task_attachments", ["workspace_id", "task_id"])

    op.create_table(
        "task_mentions",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("task_event_id", uuid_type, nullable=False),
        sa.Column("mentioned_user_id", uuid_type, nullable=False),
        sa.Column("label_snapshot", sa.String(length=240), nullable=False),
        sa.Column("mentioned_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_event_id"], ["task_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["mentioned_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["mentioned_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_event_id", "mentioned_user_id", name="uq_task_event_mention"),
    )
    for column in ("workspace_id", "matter_id", "task_id", "task_event_id", "mentioned_user_id"):
        op.create_index(f"ix_task_mentions_{column}", "task_mentions", [column])
    op.create_index("ix_task_mentions_workspace_task", "task_mentions", ["workspace_id", "task_id"])

    if op.get_bind().dialect.name == "postgresql":
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
    for table in (
        "task_checklists", "task_checklist_items", "task_labels",
        "task_label_assignments", "task_dependencies", "task_attachments", "task_mentions",
    ):
        _enable_rls(table)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        advanced_event_count = op.get_bind().execute(
            sa.text(
                "SELECT count(*) FROM task_events WHERE event_type IN "
                "('checklist_changed','labels_changed','dependency_changed','attachment_changed')"
            )
        ).scalar_one()
        if advanced_event_count:
            raise RuntimeError(
                "Cannot downgrade legal task resources while their audit events exist. "
                "Preserve or reclassify the history explicitly first."
            )
    tables = (
        "task_mentions", "task_attachments", "task_dependencies",
        "task_label_assignments", "task_labels", "task_checklist_items", "task_checklists",
    )
    for table in tables:
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f'DROP POLICY IF EXISTS "workspace_current_id" ON "{table}"')
    for table in tables:
        op.drop_table(table)
    op.drop_constraint("fk_tasks_parent_same_matter", "tasks", type_="foreignkey")
    op.drop_index("ix_tasks_parent_task_id", table_name="tasks")
    op.drop_column("tasks", "parent_task_id")
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "task_events" DROP CONSTRAINT IF EXISTS "task_event_type_enum"')
        op.execute(
            """ALTER TABLE "task_events" ADD CONSTRAINT "task_event_type_enum"
            CHECK (event_type IN (
              'created','updated','participants_changed','participant_state_changed',
              'started','blocked','unblocked','review_submitted','review_approved',
              'review_changes_requested','completed','reopened','cancelled','comment_added'
            ))"""
        )
