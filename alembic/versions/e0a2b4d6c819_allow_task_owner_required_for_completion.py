"""Allow a task owner to be a required executor.

Revision ID: e0a2b4d6c819
Revises: d9f1a3c5b617
Create Date: 2026-07-30

The original collaboration migration limited ``required_for_completion`` to
assignees.  The published product contract also permits the single active
owner to participate as a required executor, so this forward-only correction
replaces the check without rewriting the already certified revisions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e0a2b4d6c819"
down_revision: str | Sequence[str] | None = "d9f1a3c5b617"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "task_participants"
CONSTRAINT = "ck_task_participants_required_assignee"
LEGACY_PREDICATE = "required_for_completion = false OR role = 'assignee'"
OWNER_PREDICATE = "required_for_completion = false OR role IN ('owner', 'assignee')"


def _replace_check(predicate: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        reflected = sa.Table(TABLE, sa.MetaData(), autoload_with=op.get_bind())
        existing = next(
            (constraint for constraint in reflected.constraints if constraint.name == CONSTRAINT),
            None,
        )
        if existing is None:
            reflected_check = next(
                (
                    constraint
                    for constraint in sa.inspect(op.get_bind()).get_check_constraints(TABLE)
                    if constraint.get("name") == CONSTRAINT
                ),
                None,
            )
            if reflected_check is None:
                raise RuntimeError(f"Required constraint is missing: {CONSTRAINT}")
            existing = sa.CheckConstraint(
                reflected_check["sqltext"],
                name=CONSTRAINT,
            )
            reflected.append_constraint(existing)
        reflected.constraints.remove(existing)
        reflected.append_constraint(sa.CheckConstraint(predicate, name=CONSTRAINT))
        with op.batch_alter_table(
            TABLE,
            recreate="always",
            copy_from=reflected,
        ):
            pass
        return
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, predicate)


def upgrade() -> None:
    _replace_check(OWNER_PREDICATE)


def downgrade() -> None:
    _require_no_required_owner_before_downgrade()
    _replace_check(LEGACY_PREDICATE)


def _require_no_required_owner_before_downgrade() -> None:
    """Refuse to reinstall a constraint contradicted by persisted owner rows."""

    owner_ids = list(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id FROM task_participants "
                "WHERE required_for_completion = true AND role = 'owner' "
                "ORDER BY id LIMIT 20"
            )
        )
        .scalars()
    )
    if owner_ids:
        raise RuntimeError(
            "Task owner downgrade was refused before replacing the check constraint; "
            "required owner participants must be reconciled explicitly first: "
            + ", ".join(str(item) for item in owner_ids)
        )
