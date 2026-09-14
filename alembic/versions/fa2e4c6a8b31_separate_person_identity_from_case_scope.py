"""Separate canonical person identity from legal case scope.

Revision ID: fa2e4c6a8b31
Revises: f9d1e3a5c720
Create Date: 2026-07-20 22:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "fa2e4c6a8b31"
down_revision: str | Sequence[str] | None = "f9d1e3a5c720"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = (
    "klm_omnichannel_identity_reviews",
    "klm_omnichannel_identity_mutation_events",
)


def _enable_rls(table_names: tuple[str, ...]) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in table_names:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def _disable_rls(table_names: tuple[str, ...]) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in table_names:
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    # Preserve the previous one-case identity association on its canonical thread.
    op.execute(
        sa.text(
            """
            UPDATE klm_omnichannel_threads
               SET matter_id = (
                   SELECT identity.matter_id
                     FROM klm_omnichannel_identities AS identity
                    WHERE identity.workspace_id = klm_omnichannel_threads.workspace_id
                      AND identity.id = klm_omnichannel_threads.identity_id
               )
             WHERE identity_id IS NOT NULL
               AND matter_id IS NULL
               AND EXISTS (
                   SELECT 1
                     FROM klm_omnichannel_identities AS identity
                    WHERE identity.workspace_id = klm_omnichannel_threads.workspace_id
                      AND identity.id = klm_omnichannel_threads.identity_id
                      AND identity.matter_id IS NOT NULL
               )
            """
        )
    )

    op.drop_index("uq_klm_thread_workspace_identity", table_name="klm_omnichannel_threads")
    op.create_index(
        "uq_klm_thread_workspace_identity_matter",
        "klm_omnichannel_threads",
        ["workspace_id", "identity_id", "matter_id"],
        unique=True,
        postgresql_where=sa.text("identity_id IS NOT NULL AND matter_id IS NOT NULL"),
        sqlite_where=sa.text("identity_id IS NOT NULL AND matter_id IS NOT NULL"),
    )
    op.create_index(
        "uq_klm_thread_workspace_identity_unscoped",
        "klm_omnichannel_threads",
        ["workspace_id", "identity_id"],
        unique=True,
        postgresql_where=sa.text("identity_id IS NOT NULL AND matter_id IS NULL"),
        sqlite_where=sa.text("identity_id IS NOT NULL AND matter_id IS NULL"),
    )

    op.drop_index(
        "ix_klm_omnichannel_identities_matter_id",
        table_name="klm_omnichannel_identities",
    )
    # SQLite cannot rebuild this referenced table with foreign keys enabled. The
    # nullable legacy column remains inert locally; PostgreSQL removes it fully.
    if op.get_bind().dialect.name != "sqlite":
        with op.batch_alter_table("klm_omnichannel_identities") as batch:
            batch.drop_constraint("fk_klm_identity_workspace_matter", type_="foreignkey")
            batch.drop_column("matter_id")

    op.create_table(
        "klm_omnichannel_identity_reviews",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("identity_id", FlexibleUUID(), nullable=True),
        sa.Column("candidate_identity_id", FlexibleUUID(), nullable=True),
        sa.Column("thread_id", FlexibleUUID(), nullable=True),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("resolved_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("review_type", sa.String(length=60), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="open"),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("evidence", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("resolution", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_identity_review_workspace_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_identity_review_workspace_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_identity_review_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_identity_review_workspace_matter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_identity_review_idempotency"
        ),
    )
    for column in (
        "workspace_id",
        "identity_id",
        "candidate_identity_id",
        "thread_id",
        "matter_id",
        "created_by_user_id",
        "resolved_by_user_id",
        "review_type",
        "status",
    ):
        op.create_index(
            f"ix_klm_omnichannel_identity_reviews_{column}",
            "klm_omnichannel_identity_reviews",
            [column],
        )
    op.create_index(
        "ix_klm_identity_review_workspace_status_type",
        "klm_omnichannel_identity_reviews",
        ["workspace_id", "status", "review_type"],
    )

    op.create_table(
        "klm_omnichannel_identity_mutation_events",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("source_identity_id", FlexibleUUID(), nullable=False),
        sa.Column("target_identity_id", FlexibleUUID(), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("operation", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column(
            "before_snapshot", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "after_snapshot", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_identity_mutation_workspace_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "target_identity_id"],
            ["klm_omnichannel_identities.workspace_id", "klm_omnichannel_identities.id"],
            name="fk_klm_identity_mutation_workspace_target",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_klm_identity_mutation_idempotency"
        ),
    )
    for column in (
        "workspace_id",
        "source_identity_id",
        "target_identity_id",
        "actor_user_id",
        "operation",
        "occurred_at",
    ):
        op.create_index(
            f"ix_klm_omnichannel_identity_mutation_events_{column}",
            "klm_omnichannel_identity_mutation_events",
            [column],
        )
    op.create_index(
        "ix_klm_identity_mutation_workspace_source_time",
        "klm_omnichannel_identity_mutation_events",
        ["workspace_id", "source_identity_id", "occurred_at"],
    )
    _enable_rls(TABLES)


def downgrade() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT identity_id
              FROM klm_omnichannel_threads
             WHERE identity_id IS NOT NULL
             GROUP BY workspace_id, identity_id
            HAVING COUNT(*) > 1
             LIMIT 1
            """
        )
    ).first()
    if duplicate:
        raise RuntimeError(
            "Não é possível rebaixar: há identidades com múltiplas conversas/casos canônicos."
        )

    _disable_rls(TABLES)
    op.drop_index(
        "ix_klm_identity_mutation_workspace_source_time",
        table_name="klm_omnichannel_identity_mutation_events",
    )
    for column in reversed(
        (
            "workspace_id",
            "source_identity_id",
            "target_identity_id",
            "actor_user_id",
            "operation",
            "occurred_at",
        )
    ):
        op.drop_index(
            f"ix_klm_omnichannel_identity_mutation_events_{column}",
            table_name="klm_omnichannel_identity_mutation_events",
        )
    op.drop_table("klm_omnichannel_identity_mutation_events")

    op.drop_index(
        "ix_klm_identity_review_workspace_status_type",
        table_name="klm_omnichannel_identity_reviews",
    )
    for column in reversed(
        (
            "workspace_id",
            "identity_id",
            "candidate_identity_id",
            "thread_id",
            "matter_id",
            "created_by_user_id",
            "resolved_by_user_id",
            "review_type",
            "status",
        )
    ):
        op.drop_index(
            f"ix_klm_omnichannel_identity_reviews_{column}",
            table_name="klm_omnichannel_identity_reviews",
        )
    op.drop_table("klm_omnichannel_identity_reviews")

    op.drop_index(
        "uq_klm_thread_workspace_identity_unscoped",
        table_name="klm_omnichannel_threads",
    )
    op.drop_index(
        "uq_klm_thread_workspace_identity_matter",
        table_name="klm_omnichannel_threads",
    )
    if op.get_bind().dialect.name != "sqlite":
        with op.batch_alter_table("klm_omnichannel_identities") as batch:
            batch.add_column(sa.Column("matter_id", FlexibleUUID(), nullable=True))
            batch.create_foreign_key(
                "fk_klm_identity_workspace_matter",
                "matters",
                ["workspace_id", "matter_id"],
                ["workspace_id", "id"],
                ondelete="RESTRICT",
            )
    op.create_index(
        "ix_klm_omnichannel_identities_matter_id",
        "klm_omnichannel_identities",
        ["matter_id"],
    )
    op.execute(
        sa.text(
            """
            UPDATE klm_omnichannel_identities
               SET matter_id = (
                   SELECT thread.matter_id
                     FROM klm_omnichannel_threads AS thread
                    WHERE thread.workspace_id = klm_omnichannel_identities.workspace_id
                      AND thread.identity_id = klm_omnichannel_identities.id
                    LIMIT 1
               )
            """
        )
    )
    op.create_index(
        "uq_klm_thread_workspace_identity",
        "klm_omnichannel_threads",
        ["workspace_id", "identity_id"],
        unique=True,
        postgresql_where=sa.text("identity_id IS NOT NULL"),
        sqlite_where=sa.text("identity_id IS NOT NULL"),
    )
