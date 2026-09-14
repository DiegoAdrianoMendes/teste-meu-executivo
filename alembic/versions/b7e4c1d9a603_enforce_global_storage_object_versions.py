"""Enforce globally unique StorageObject versions.

Revision ID: b7e4c1d9a603
Revises: a9d2e4f6b508
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e4c1d9a603"
down_revision: str | Sequence[str] | None = "a9d2e4f6b508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "uq_storage_object_object_version"


def _require_global_version_uniqueness() -> None:
    bind = op.get_bind()
    duplicate_groups = int(
        bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT object_id, version
                    FROM storage_objects
                    GROUP BY object_id, version
                    HAVING COUNT(*) > 1
                ) AS duplicate_versions
                """
            )
        ).scalar_one()
    )
    if duplicate_groups == 0:
        return
    examples = bind.execute(
        sa.text(
            """
            SELECT object_id, version, COUNT(*) AS row_count
            FROM storage_objects
            GROUP BY object_id, version
            HAVING COUNT(*) > 1
            ORDER BY object_id, version
            LIMIT 10
            """
        )
    ).all()
    rendered = ", ".join(
        f"object_id={row.object_id}:version={row.version}:rows={row.row_count}"
        for row in examples
    )
    raise RuntimeError(
        "Global storage version migration blocked by legacy duplicates: "
        f"duplicate_groups={duplicate_groups}; examples=[{rendered}]. "
        "Resolve ownership explicitly before retrying the migration."
    )


def upgrade() -> None:
    _require_global_version_uniqueness()
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("storage_objects", recreate="always") as batch_op:
            batch_op.create_unique_constraint(
                CONSTRAINT_NAME,
                ["object_id", "version"],
            )
        return
    op.create_unique_constraint(
        CONSTRAINT_NAME,
        "storage_objects",
        ["object_id", "version"],
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("storage_objects", recreate="always") as batch_op:
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
        return
    op.drop_constraint(
        CONSTRAINT_NAME,
        "storage_objects",
        type_="unique",
    )
