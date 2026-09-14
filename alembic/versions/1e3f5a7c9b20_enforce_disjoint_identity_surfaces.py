"""enforce disjoint platform and workspace identities

Revision ID: 1e3f5a7c9b20
Revises: 0d2f4a6c8e10
Create Date: 2026-07-24 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "1e3f5a7c9b20"
down_revision: str | Sequence[str] | None = "0d2f4a6c8e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM workspace_memberships membership
                JOIN users user_record ON user_record.id = membership.user_id
                WHERE user_record.is_platform_admin = true
                   OR user_record.is_internal_staff = true
                   OR user_record.platform_role_package IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Existem identidades vinculadas simultaneamente ao Blackbox e a workspaces';
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_platform_identity_membership()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM users user_record
                WHERE user_record.id = NEW.user_id
                  AND (
                      user_record.is_platform_admin = true
                      OR user_record.is_internal_staff = true
                      OR user_record.platform_role_package IS NOT NULL
                  )
            ) THEN
                RAISE EXCEPTION
                    'Identidade Blackbox não pode possuir membership de workspace'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workspace_membership_reject_platform_identity
        BEFORE INSERT OR UPDATE OF user_id
        ON workspace_memberships
        FOR EACH ROW
        EXECUTE FUNCTION reject_platform_identity_membership();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_dual_surface_user()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF (
                NEW.is_platform_admin = true
                OR NEW.is_internal_staff = true
                OR NEW.platform_role_package IS NOT NULL
            ) AND EXISTS (
                SELECT 1
                FROM workspace_memberships membership
                WHERE membership.user_id = NEW.id
            ) THEN
                RAISE EXCEPTION
                    'Usuário de workspace não pode ser convertido em identidade Blackbox'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_user_reject_dual_surface_identity
        BEFORE INSERT OR UPDATE OF
            is_platform_admin,
            is_internal_staff,
            platform_role_package
        ON users
        FOR EACH ROW
        EXECUTE FUNCTION reject_dual_surface_user();
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "DROP TRIGGER IF EXISTS trg_user_reject_dual_surface_identity ON users"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS reject_dual_surface_user()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_workspace_membership_reject_platform_identity "
        "ON workspace_memberships"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS reject_platform_identity_membership()"
    )
