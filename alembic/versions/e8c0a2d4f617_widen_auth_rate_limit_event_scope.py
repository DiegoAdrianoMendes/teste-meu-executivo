"""Widen the persisted auth rate-limit event scope.

Revision ID: e8c0a2d4f617
Revises: e7b9d1f3a526

The identity lifecycle added ``accept_invitation`` to the event CHECK catalog,
but the legacy physical column remained ``VARCHAR(15)``.  The value is 17
characters long, so PostgreSQL rejected the audit event before invitation
acceptance reached tenant resolution.

This expand-only revision changes only the column width.  PostgreSQL preserves
the existing CHECK, indexes, grants and RLS metadata because the table is not
recreated.  Downgrade refuses to contract while any durable value would no
longer fit; it never truncates or deletes evidence.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8c0a2d4f617"
down_revision: str | Sequence[str] | None = "e7b9d1f3a526"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "auth_rate_limit_events"
COLUMN_NAME = "scope"
CHECK_NAME = "auth_rate_limit_scope_enum"
AUTH_RATE_LIMIT_SCOPES = (
    "login",
    "register",
    "verify_signup",
    "invitation",
    "accept_invitation",
    "forgot_password",
    "reset_password",
)
PREVIOUS_SCOPE_LENGTH = 15
SCOPE_LENGTH = max(map(len, AUTH_RATE_LIMIT_SCOPES))


def _scope_check() -> str:
    allowed = ", ".join(f"'{scope}'" for scope in AUTH_RATE_LIMIT_SCOPES)
    return f"scope IN ({allowed})"


def _scope_length() -> int | None:
    columns = sa.inspect(op.get_bind()).get_columns(TABLE_NAME)
    scope = next(column for column in columns if column["name"] == COLUMN_NAME)
    return getattr(scope["type"], "length", None)


def _require_scope_length(expected: int) -> None:
    actual = _scope_length()
    if actual != expected:
        raise RuntimeError(
            "Auth rate-limit event scope width drift blocks migration; "
            f"expected VARCHAR({expected}), found {actual!r}"
        )


def _alter_scope_length(*, previous_length: int, target_length: int) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(TABLE_NAME, recreate="always") as batch_op:
            # SQLite batch reflection includes the table's final parenthesis in
            # this legacy CHECK.  Recreate the same catalog explicitly while
            # rebuilding solely for the width change.
            batch_op.drop_constraint(CHECK_NAME, type_="check")
            batch_op.alter_column(
                COLUMN_NAME,
                existing_type=sa.String(length=previous_length),
                type_=sa.String(length=target_length),
                existing_nullable=False,
            )
            batch_op.create_check_constraint(CHECK_NAME, _scope_check())
    else:
        op.alter_column(
            TABLE_NAME,
            COLUMN_NAME,
            existing_type=sa.String(length=previous_length),
            type_=sa.String(length=target_length),
            existing_nullable=False,
        )


def _require_no_oversized_scope_before_downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Serialize the evidence check with concurrent writers.  The following
        # ALTER reuses the same transaction and therefore the same table lock.
        bind.execute(sa.text(f'LOCK TABLE "{TABLE_NAME}" IN ACCESS EXCLUSIVE MODE'))
    oversized = int(
        bind.execute(
            sa.text(
                f'SELECT COUNT(*) FROM "{TABLE_NAME}" '
                f'WHERE length("{COLUMN_NAME}") > :maximum_length'
            ),
            {"maximum_length": PREVIOUS_SCOPE_LENGTH},
        ).scalar_one()
    )
    if oversized:
        raise RuntimeError(
            "Cannot downgrade auth rate-limit event scope width while durable "
            f"evidence exceeds {PREVIOUS_SCOPE_LENGTH} characters: {oversized} row(s)"
        )


def upgrade() -> None:
    _require_scope_length(PREVIOUS_SCOPE_LENGTH)
    _alter_scope_length(
        previous_length=PREVIOUS_SCOPE_LENGTH,
        target_length=SCOPE_LENGTH,
    )
    _require_scope_length(SCOPE_LENGTH)


def downgrade() -> None:
    _require_scope_length(SCOPE_LENGTH)
    _require_no_oversized_scope_before_downgrade()
    _alter_scope_length(
        previous_length=SCOPE_LENGTH,
        target_length=PREVIOUS_SCOPE_LENGTH,
    )
    _require_scope_length(PREVIOUS_SCOPE_LENGTH)
