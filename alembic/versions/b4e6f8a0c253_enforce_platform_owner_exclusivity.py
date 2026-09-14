"""enforce one explicitly identified platform owner

Revision ID: b4e6f8a0c253
Revises: a3d5e7f9b142
Create Date: 2026-07-31 02:10:00.000000

The historical e7 migration promoted every legacy ``is_platform_admin`` row
to ``platform_owner``.  Editing that published migration would make applied
databases diverge, so this forward correction fails closed when more than one
candidate exists unless the operator explicitly identifies the titular by
``KLM_PLATFORM_OWNER_USER_ID``.  Every other historical owner is reduced to
the operational ``platform_admin`` package before a database uniqueness guard
is installed.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa

from alembic import op

revision: str = "b4e6f8a0c253"
down_revision: str | Sequence[str] | None = "a3d5e7f9b142"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_SELECTOR_ENV = "KLM_PLATFORM_OWNER_USER_ID"
OWNER_PACKAGE = "platform_owner"
ADMIN_PACKAGE = "platform_admin"
UNIQUE_INDEX = "uq_users_single_platform_owner"


def _selected_owner_id() -> str | None:
    raw_value = os.getenv(OWNER_SELECTOR_ENV, "").strip()
    if not raw_value:
        return None
    try:
        return str(UUID(raw_value))
    except ValueError as exc:
        raise RuntimeError(
            f"{OWNER_SELECTOR_ENV} must be a canonical user UUID."
        ) from exc


def _owner_rows(bind: sa.Connection) -> list[sa.Row]:
    return list(
        bind.execute(
            sa.text(
                """
                SELECT id,
                       is_platform_admin,
                       is_internal_staff,
                       platform_access_active,
                       is_active,
                       deleted_at
                FROM users
                WHERE platform_role_package = :owner_package
                ORDER BY id
                """
            ),
            {"owner_package": OWNER_PACKAGE},
        )
    )


def _eligible_candidate(bind: sa.Connection, candidate_id: str) -> sa.Row:
    candidate = bind.execute(
        sa.text(
            """
            SELECT id,
                   is_platform_admin,
                   is_internal_staff,
                   platform_access_active,
                   is_active,
                   deleted_at
            FROM users
            WHERE id = :candidate_id
            """
        ),
        {"candidate_id": candidate_id},
    ).one_or_none()
    if candidate is None:
        raise RuntimeError(
            f"{OWNER_SELECTOR_ENV} does not identify an existing user."
        )
    if not bool(candidate.is_platform_admin):
        raise RuntimeError(
            f"{OWNER_SELECTOR_ENV} must identify an existing legacy platform admin."
        )
    return candidate


def _assert_root_identity_is_safe(bind: sa.Connection, candidate: sa.Row) -> None:
    if not (
        bool(candidate.is_platform_admin)
        and bool(candidate.is_internal_staff)
        and bool(candidate.platform_access_active)
        and bool(candidate.is_active)
        and candidate.deleted_at is None
    ):
        raise RuntimeError(
            "The selected platform owner is inactive, deleted, or lacks the "
            "complete internal platform authority markers. Reconcile that "
            "identity explicitly before retrying the migration."
        )
    membership_exists = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM workspace_memberships
            WHERE user_id = :candidate_id
            LIMIT 1
            """
        ),
        {"candidate_id": str(candidate.id)},
    ).first()
    if membership_exists is not None:
        raise RuntimeError(
            "The selected platform owner has a workspace membership. The "
            "Blackbox identity must remain disjoint from customer identities."
        )


def _reconcile_legacy_owner_rows() -> None:
    bind = op.get_bind()
    owners = _owner_rows(bind)
    selected_id = _selected_owner_id()

    if not owners:
        if selected_id is None:
            legacy_admin_count = int(
                bind.execute(
                    sa.text(
                        "SELECT COUNT(*) FROM users WHERE is_platform_admin = true"
                    )
                ).scalar_one()
            )
            if legacy_admin_count:
                raise RuntimeError(
                    "No platform owner is currently identified, but legacy platform "
                    f"admins exist. Set {OWNER_SELECTOR_ENV} to the titular user UUID "
                    "and rerun the migration."
                )
            # A new greenfield database is migrated before its single root seed.
            return
        selected = _eligible_candidate(bind, selected_id)
    else:
        owner_by_id = {str(row.id): row for row in owners}
        if selected_id is None:
            if len(owners) != 1:
                raise RuntimeError(
                    "Multiple historical platform owners were found. Set "
                    f"{OWNER_SELECTOR_ENV} to the titular user UUID; no owner was "
                    "selected or reclassified."
                )
            selected = owners[0]
            selected_id = str(selected.id)
        else:
            selected = owner_by_id.get(selected_id)
            if selected is None:
                selected = _eligible_candidate(bind, selected_id)

    _assert_root_identity_is_safe(bind, selected)
    assert selected_id is not None

    bind.execute(
        sa.text(
            """
            UPDATE users
            SET platform_role_package = :admin_package
            WHERE platform_role_package = :owner_package
              AND id <> :selected_id
            """
        ),
        {
            "admin_package": ADMIN_PACKAGE,
            "owner_package": OWNER_PACKAGE,
            "selected_id": selected_id,
        },
    )
    bind.execute(
        sa.text(
            """
            UPDATE users
            SET platform_role_package = :owner_package,
                is_platform_admin = true,
                is_internal_staff = true,
                platform_access_active = true
            WHERE id = :selected_id
            """
        ),
        {"owner_package": OWNER_PACKAGE, "selected_id": selected_id},
    )

    remaining = list(
        bind.execute(
            sa.text(
                """
                SELECT id
                FROM users
                WHERE platform_role_package = :owner_package
                ORDER BY id
                """
            ),
            {"owner_package": OWNER_PACKAGE},
        ).scalars()
    )
    if [str(item) for item in remaining] != [selected_id]:
        raise RuntimeError(
            "Platform owner reconciliation did not produce exactly the explicitly "
            "identified titular. The uniqueness guard was not installed."
        )


def upgrade() -> None:
    _reconcile_legacy_owner_rows()
    owner_predicate = sa.text("platform_role_package = 'platform_owner'")
    op.create_index(
        UNIQUE_INDEX,
        "users",
        ["platform_role_package"],
        unique=True,
        postgresql_where=owner_predicate,
        sqlite_where=owner_predicate,
    )


def downgrade() -> None:
    # Dropping the schema guard must never recreate the historical ambiguity.
    op.drop_index(UNIQUE_INDEX, table_name="users")
