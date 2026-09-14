"""adiciona continuidade auditável de funcionários

Revision ID: 2f4a6c8e0b31
Revises: 1e3f5a7c9b20
Create Date: 2026-07-24 20:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "2f4a6c8e0b31"
down_revision: str | Sequence[str] | None = "1e3f5a7c9b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_POLICY = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"
PLATFORM_SCOPE_SETTING = "app.current_platform_scope"
PLATFORM_ACTOR_SETTING = "app.current_platform_actor_id"
PLATFORM_ACTION_SETTING = "app.current_platform_action"
PLATFORM_STAFF_REASSIGNMENT_SCOPE = "staff_reassignment"
PLATFORM_STAFF_REASSIGNMENT_ACTIONS = (
    "staff_reassignment:approve",
    "staff_reassignment:view",
)
PLATFORM_SUPPORT_SCOPE = "support"
PLATFORM_SUPPORT_ACTIONS = (
    "support:assign",
    "support:export",
    "support:manage",
    "support:respond",
    "support:triage",
    "support:view",
)
WORKSPACE_TABLES = (
    "workspace_staff_reassignment_plans",
    "workspace_staff_reassignment_items",
    "workspace_staff_reassignment_events",
)
PLATFORM_TABLES = (
    "platform_staff_reassignment_plans",
    "platform_staff_reassignment_items",
    "platform_staff_reassignment_events",
)
IMMUTABLE_TABLES = (
    "workspace_staff_reassignment_events",
    "platform_staff_reassignment_events",
)


def _workspace_predicate() -> str:
    return (
        f"workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )


def _platform_predicate(
    *,
    scope: str,
    actions: tuple[str, ...],
) -> str:
    allowed_actions = " OR ".join(
        f"current_setting('{PLATFORM_ACTION_SETTING}', true) = '{action}'"
        for action in actions
    )
    return (
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = '{scope}' AND "
        f"NULLIF(current_setting('{PLATFORM_ACTOR_SETTING}', true), '') IS NOT NULL "
        f"AND ({allowed_actions})"
    )


def _support_ticket_predicate(*, allow_reassignment: bool) -> str:
    predicates = [
        _workspace_predicate(),
        _platform_predicate(
            scope=PLATFORM_SUPPORT_SCOPE,
            actions=PLATFORM_SUPPORT_ACTIONS,
        ),
    ]
    if allow_reassignment:
        predicates.append(
            _platform_predicate(
                scope=PLATFORM_STAFF_REASSIGNMENT_SCOPE,
                actions=PLATFORM_STAFF_REASSIGNMENT_ACTIONS,
            )
        )
    return " OR ".join(f"({predicate})" for predicate in predicates)


def _replace_support_ticket_policy(*, allow_reassignment: bool) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = _support_ticket_predicate(
        allow_reassignment=allow_reassignment
    )
    op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "support_tickets"')
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "support_tickets"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in WORKSPACE_TABLES:
        predicate = _workspace_predicate()
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )
    platform_predicate = _platform_predicate(
        scope=PLATFORM_STAFF_REASSIGNMENT_SCOPE,
        actions=PLATFORM_STAFF_REASSIGNMENT_ACTIONS,
    )
    for table_name in PLATFORM_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({platform_predicate}) WITH CHECK ({platform_predicate})'''
        )
    _replace_support_ticket_policy(allow_reassignment=True)


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _replace_support_ticket_policy(allow_reassignment=False)
    for table_name in reversed(WORKSPACE_TABLES + PLATFORM_TABLES):
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def _enable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION klm_reject_append_only_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table_name in IMMUTABLE_TABLES:
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable"
                BEFORE UPDATE OR DELETE ON "{table_name}"
                FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable_truncate"
                BEFORE TRUNCATE ON "{table_name}"
                FOR EACH STATEMENT EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
        return
    if dialect == "sqlite":
        for table_name in IMMUTABLE_TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    f'''
                    CREATE TRIGGER IF NOT EXISTS
                    "trg_{table_name}_immutable_{operation}"
                    BEFORE {operation.upper()} ON "{table_name}"
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            '{table_name} possui auditoria imutável'
                        );
                    END
                    '''
                )


def _disable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table_name in reversed(IMMUTABLE_TABLES):
            op.execute(
                f'DROP TRIGGER IF EXISTS '
                f'"trg_{table_name}_immutable_truncate" ON "{table_name}"'
            )
            op.execute(
                f'DROP TRIGGER IF EXISTS '
                f'"trg_{table_name}_immutable" ON "{table_name}"'
            )
        return
    if dialect == "sqlite":
        for table_name in reversed(IMMUTABLE_TABLES):
            for operation in ("update", "delete"):
                op.execute(
                    f'DROP TRIGGER IF EXISTS '
                    f'"trg_{table_name}_immutable_{operation}"'
                )


def _plan_columns(*, workspace: bool) -> list[sa.Column]:
    columns: list[sa.Column] = []
    if workspace:
        columns.append(
            sa.Column("workspace_id", FlexibleUUID(), nullable=False)
        )
    columns.extend(
        [
            sa.Column("source_user_id", FlexibleUUID(), nullable=False),
            sa.Column("target_user_id", FlexibleUUID(), nullable=False),
            sa.Column("mode", sa.String(length=20), nullable=False),
            sa.Column(
                "status",
                sa.String(length=30),
                server_default="applied",
                nullable=False,
            ),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("tag_label", sa.String(length=120), nullable=False),
            sa.Column("scopes", FlexibleJSON(), nullable=False),
            sa.Column("assignment_summary", FlexibleJSON(), nullable=False),
            sa.Column(
                "conflict_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            ),
            sa.Column(
                "starts_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "applied_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("restore_reason", sa.Text(), nullable=True),
            sa.Column("requested_by_user_id", FlexibleUUID(), nullable=False),
            sa.Column("approved_by_user_id", FlexibleUUID(), nullable=False),
            sa.Column(
                "idempotency_key",
                sa.String(length=180),
                nullable=False,
            ),
            sa.Column(
                "request_fingerprint",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column(
                "version",
                sa.Integer(),
                server_default="1",
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("id", FlexibleUUID(), nullable=False),
        ]
    )
    return columns


def _item_columns(*, workspace: bool) -> list[sa.Column]:
    columns: list[sa.Column] = []
    if workspace:
        columns.append(
            sa.Column("workspace_id", FlexibleUUID(), nullable=False)
        )
    columns.extend(
        [
            sa.Column("plan_id", FlexibleUUID(), nullable=False),
            sa.Column("entity_type", sa.String(length=80), nullable=False),
            sa.Column("entity_id", FlexibleUUID(), nullable=False),
            sa.Column(
                "assignment_field",
                sa.String(length=80),
                nullable=False,
            ),
            sa.Column("previous_user_id", FlexibleUUID(), nullable=False),
            sa.Column("assigned_user_id", FlexibleUUID(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=20),
                server_default="applied",
                nullable=False,
            ),
            sa.Column("tag_label", sa.String(length=120), nullable=False),
            sa.Column("snapshot", FlexibleJSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("id", FlexibleUUID(), nullable=False),
        ]
    )
    return columns


def _event_columns(*, workspace: bool) -> list[sa.Column]:
    columns: list[sa.Column] = []
    if workspace:
        columns.append(
            sa.Column("workspace_id", FlexibleUUID(), nullable=False)
        )
    columns.extend(
        [
            sa.Column("plan_id", FlexibleUUID(), nullable=False),
            sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
            sa.Column("action", sa.String(length=60), nullable=False),
            sa.Column("details", FlexibleJSON(), nullable=False),
            sa.Column(
                "occurred_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("id", FlexibleUUID(), nullable=False),
        ]
    )
    return columns


def _create_workspace_tables() -> None:
    op.create_table(
        "workspace_staff_reassignment_plans",
        *_plan_columns(workspace=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "requested_by_user_id",
            "idempotency_key",
            name="uq_workspace_staff_reassignment_idempotency",
        ),
        sa.CheckConstraint(
            "source_user_id <> target_user_id",
            name="ck_workspace_staff_reassignment_distinct_users",
        ),
        sa.CheckConstraint(
            "mode IN ('temporary', 'permanent')",
            name="ck_workspace_staff_reassignment_mode",
        ),
        sa.CheckConstraint(
            "(mode = 'temporary' AND ends_at IS NOT NULL) OR "
            "(mode = 'permanent' AND ends_at IS NULL)",
            name="ck_workspace_staff_reassignment_window",
        ),
        sa.CheckConstraint(
            "status IN ('applied', 'restored', 'partially_restored', 'cancelled')",
            name="ck_workspace_staff_reassignment_status",
        ),
    )
    op.create_table(
        "workspace_staff_reassignment_items",
        *_item_columns(workspace=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["workspace_staff_reassignment_plans.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "entity_type",
            "entity_id",
            "assignment_field",
            name="uq_workspace_staff_reassignment_item",
        ),
        sa.CheckConstraint(
            "status IN ('applied', 'restored', 'conflict')",
            name="ck_workspace_staff_reassignment_item_status",
        ),
    )
    op.create_table(
        "workspace_staff_reassignment_events",
        *_event_columns(workspace=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["workspace_staff_reassignment_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def _create_platform_tables() -> None:
    op.create_table(
        "platform_staff_reassignment_plans",
        *_plan_columns(workspace=False),
        sa.ForeignKeyConstraint(
            ["source_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "requested_by_user_id",
            "idempotency_key",
            name="uq_platform_staff_reassignment_idempotency",
        ),
        sa.CheckConstraint(
            "source_user_id <> target_user_id",
            name="ck_platform_staff_reassignment_distinct_users",
        ),
        sa.CheckConstraint(
            "mode IN ('temporary', 'permanent')",
            name="ck_platform_staff_reassignment_mode",
        ),
        sa.CheckConstraint(
            "(mode = 'temporary' AND ends_at IS NOT NULL) OR "
            "(mode = 'permanent' AND ends_at IS NULL)",
            name="ck_platform_staff_reassignment_window",
        ),
        sa.CheckConstraint(
            "status IN ('applied', 'restored', 'partially_restored', 'cancelled')",
            name="ck_platform_staff_reassignment_status",
        ),
    )
    op.create_table(
        "platform_staff_reassignment_items",
        *_item_columns(workspace=False),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["platform_staff_reassignment_plans.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "entity_type",
            "entity_id",
            "assignment_field",
            name="uq_platform_staff_reassignment_item",
        ),
        sa.CheckConstraint(
            "status IN ('applied', 'restored', 'conflict')",
            name="ck_platform_staff_reassignment_item_status",
        ),
    )
    op.create_table(
        "platform_staff_reassignment_events",
        *_event_columns(workspace=False),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["platform_staff_reassignment_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def _create_indexes() -> None:
    indexes: dict[str, tuple[str, tuple[str, ...]]] = {
        "ix_workspace_staff_reassignment_plans_workspace_id": (
            "workspace_staff_reassignment_plans",
            ("workspace_id",),
        ),
        "ix_workspace_staff_reassignment_plans_source_user_id": (
            "workspace_staff_reassignment_plans",
            ("source_user_id",),
        ),
        "ix_workspace_staff_reassignment_plans_target_user_id": (
            "workspace_staff_reassignment_plans",
            ("target_user_id",),
        ),
        "ix_workspace_staff_reassignment_source_status": (
            "workspace_staff_reassignment_plans",
            ("workspace_id", "source_user_id", "status"),
        ),
        "ix_workspace_staff_reassignment_items_workspace_id": (
            "workspace_staff_reassignment_items",
            ("workspace_id",),
        ),
        "ix_workspace_staff_reassignment_items_plan_id": (
            "workspace_staff_reassignment_items",
            ("plan_id",),
        ),
        "ix_workspace_staff_reassignment_item_plan_status": (
            "workspace_staff_reassignment_items",
            ("workspace_id", "plan_id", "status"),
        ),
        "ix_workspace_staff_reassignment_events_workspace_id": (
            "workspace_staff_reassignment_events",
            ("workspace_id",),
        ),
        "ix_workspace_staff_reassignment_events_plan_id": (
            "workspace_staff_reassignment_events",
            ("plan_id",),
        ),
        "ix_workspace_staff_reassignment_events_actor_user_id": (
            "workspace_staff_reassignment_events",
            ("actor_user_id",),
        ),
        "ix_workspace_staff_reassignment_events_action": (
            "workspace_staff_reassignment_events",
            ("action",),
        ),
        "ix_workspace_staff_reassignment_events_occurred_at": (
            "workspace_staff_reassignment_events",
            ("occurred_at",),
        ),
        "ix_workspace_staff_reassignment_event_history": (
            "workspace_staff_reassignment_events",
            ("workspace_id", "plan_id", "occurred_at"),
        ),
        "ix_platform_staff_reassignment_plans_source_user_id": (
            "platform_staff_reassignment_plans",
            ("source_user_id",),
        ),
        "ix_platform_staff_reassignment_plans_target_user_id": (
            "platform_staff_reassignment_plans",
            ("target_user_id",),
        ),
        "ix_platform_staff_reassignment_source_status": (
            "platform_staff_reassignment_plans",
            ("source_user_id", "status"),
        ),
        "ix_platform_staff_reassignment_items_plan_id": (
            "platform_staff_reassignment_items",
            ("plan_id",),
        ),
        "ix_platform_staff_reassignment_item_plan_status": (
            "platform_staff_reassignment_items",
            ("plan_id", "status"),
        ),
        "ix_platform_staff_reassignment_events_plan_id": (
            "platform_staff_reassignment_events",
            ("plan_id",),
        ),
        "ix_platform_staff_reassignment_events_actor_user_id": (
            "platform_staff_reassignment_events",
            ("actor_user_id",),
        ),
        "ix_platform_staff_reassignment_events_action": (
            "platform_staff_reassignment_events",
            ("action",),
        ),
        "ix_platform_staff_reassignment_events_occurred_at": (
            "platform_staff_reassignment_events",
            ("occurred_at",),
        ),
        "ix_platform_staff_reassignment_event_history": (
            "platform_staff_reassignment_events",
            ("plan_id", "occurred_at"),
        ),
    }
    for index_name, (table_name, columns) in indexes.items():
        op.create_index(index_name, table_name, list(columns))


def upgrade() -> None:
    _create_workspace_tables()
    _create_platform_tables()
    _create_indexes()
    _enable_rls()
    _enable_append_only_guards()


def downgrade() -> None:
    _disable_append_only_guards()
    _disable_rls()
    for table_name in reversed(WORKSPACE_TABLES + PLATFORM_TABLES):
        op.drop_table(table_name)
