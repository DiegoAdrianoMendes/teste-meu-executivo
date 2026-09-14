"""Consolidate identity isolation, revocation, and tenant bootstrap.

Revision ID: c8f5d2a9b714
Revises: b7e4c1d9a603
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "c8f5d2a9b714"
down_revision: str | Sequence[str] | None = "b7e4c1d9a603"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
IDENTITY_TABLES = ("signup_verifications", "workspace_invitations")
SIGNUP_RESOLVER = "klm_resolve_signup_verification_workspace(text,uuid)"
INVITATION_RESOLVER = "klm_resolve_workspace_invitation_workspace(uuid,text,uuid)"
SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

auth_revocation_intent_status_enum = sa.Enum(
    "pending",
    "completed",
    name="auth_revocation_intent_status_enum",
    native_enum=False,
    create_constraint=True,
)


def _scalar_count(statement: str) -> int:
    return int(op.get_bind().execute(sa.text(statement)).scalar_one())


def _require_upgrade_consistency() -> None:
    email_octet_length = (
        "octet_length(email)"
        if op.get_bind().dialect.name == "postgresql"
        else "length(CAST(email AS BLOB))"
    )
    violations = {
        "users.email_casefold": _scalar_count(
            """
            SELECT COUNT(*)
            FROM (
                SELECT lower(email)
                FROM users
                WHERE email IS NOT NULL
                GROUP BY lower(email)
                HAVING COUNT(*) > 1
            ) AS duplicate_emails
            """
        ),
        "users.email_canonical_ascii": _scalar_count(
            f"""
            SELECT COUNT(*)
            FROM users
            WHERE email IS NOT NULL
              AND (
                    email <> lower(email)
                    OR email <> trim(email)
                    OR length(email) <> {email_octet_length}
              )
            """
        ),
        "signup_verifications.workspace_user": _scalar_count(
            """
            SELECT COUNT(*)
            FROM signup_verifications AS verification
            WHERE NOT EXISTS (
                SELECT 1
                FROM workspace_memberships AS membership
                WHERE membership.workspace_id = verification.workspace_id
                  AND membership.user_id = verification.user_id
            )
            """
        ),
        "workspace_invitations.membership_identity": _scalar_count(
            """
            SELECT COUNT(*)
            FROM workspace_invitations AS invitation
            WHERE NOT EXISTS (
                SELECT 1
                FROM workspace_memberships AS membership
                WHERE membership.id = invitation.membership_id
                  AND membership.workspace_id = invitation.workspace_id
                  AND membership.user_id = invitation.user_id
            )
            """
        ),
    }
    violations = {label: count for label, count in violations.items() if count > 0}
    if violations:
        details = ", ".join(
            f"{label}={count}" for label, count in sorted(violations.items())
        )
        raise RuntimeError(
            "Identity security migration blocked by inconsistent rows: " + details
        )


def _add_user_email_constraint() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "users",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.create_check_constraint(
                "ck_users_email_canonical_ascii_lowercase",
                "email IS NULL OR ("
                "email = lower(email) AND "
                "email = trim(email) AND "
                "length(email) = length(CAST(email AS BLOB))"
                ")",
            )
        return
    op.create_check_constraint(
        "ck_users_email_canonical_ascii_lowercase",
        "users",
        "email IS NULL OR ("
        "email = lower(email) AND "
        "email = btrim(email) AND "
        "char_length(email) = octet_length(email)"
        ")",
    )


def _drop_user_email_constraint() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "users",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "ck_users_email_canonical_ascii_lowercase",
                type_="check",
            )
        return
    op.drop_constraint(
        "ck_users_email_canonical_ascii_lowercase",
        "users",
        type_="check",
    )


def _require_downgrade_safe() -> None:
    lossy_state = {
        "users.auth_epoch": _scalar_count("SELECT COUNT(*) FROM users WHERE auth_epoch <> 0"),
        "identity_outbox_messages.request_id": _scalar_count(
            "SELECT COUNT(*) FROM identity_outbox_messages WHERE request_id IS NOT NULL"
        ),
        "workspace_invitations.security_state": _scalar_count(
            """
            SELECT COUNT(*)
            FROM workspace_invitations
            WHERE failed_attempt_count <> 0 OR locked_at IS NOT NULL
            """
        ),
        "auth_revocation_intents": _scalar_count(
            "SELECT COUNT(*) FROM auth_revocation_intents"
        ),
    }
    lossy_state = {label: count for label, count in lossy_state.items() if count > 0}
    if lossy_state:
        details = ", ".join(
            f"{label}={count}" for label, count in sorted(lossy_state.items())
        )
        raise RuntimeError(
            "Identity security downgrade blocked to preserve security state: " + details
        )


def _add_identity_constraints() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "workspace_memberships",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.create_unique_constraint(
                "uq_workspace_membership_identity_binding",
                ["id", "workspace_id", "user_id"],
            )
        with op.batch_alter_table(
            "signup_verifications",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.create_foreign_key(
                "fk_signup_verification_workspace_user_membership",
                "workspace_memberships",
                ["workspace_id", "user_id"],
                ["workspace_id", "user_id"],
                ondelete="CASCADE",
            )
        with op.batch_alter_table(
            "workspace_invitations",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.create_foreign_key(
                "fk_workspace_invitation_membership_identity_binding",
                "workspace_memberships",
                ["membership_id", "workspace_id", "user_id"],
                ["id", "workspace_id", "user_id"],
                ondelete="CASCADE",
            )
        return

    op.create_unique_constraint(
        "uq_workspace_membership_identity_binding",
        "workspace_memberships",
        ["id", "workspace_id", "user_id"],
    )
    op.create_foreign_key(
        "fk_signup_verification_workspace_user_membership",
        "signup_verifications",
        "workspace_memberships",
        ["workspace_id", "user_id"],
        ["workspace_id", "user_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_workspace_invitation_membership_identity_binding",
        "workspace_invitations",
        "workspace_memberships",
        ["membership_id", "workspace_id", "user_id"],
        ["id", "workspace_id", "user_id"],
        ondelete="CASCADE",
    )


def _drop_identity_constraints() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "workspace_invitations",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_workspace_invitation_membership_identity_binding",
                type_="foreignkey",
            )
        with op.batch_alter_table(
            "signup_verifications",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_signup_verification_workspace_user_membership",
                type_="foreignkey",
            )
        with op.batch_alter_table(
            "workspace_memberships",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "uq_workspace_membership_identity_binding",
                type_="unique",
            )
        return

    op.drop_constraint(
        "fk_workspace_invitation_membership_identity_binding",
        "workspace_invitations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_signup_verification_workspace_user_membership",
        "signup_verifications",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_workspace_membership_identity_binding",
        "workspace_memberships",
        type_="unique",
    )


def _enable_identity_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        "workspace_id::text = "
        "NULLIF(current_setting('app.current_workspace_id', true), '')"
    )
    for table_name in IDENTITY_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""
            CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
            AS PERMISSIVE
            FOR ALL
            TO PUBLIC
            USING ({predicate})
            WITH CHECK ({predicate})
            """
        )


def _disable_identity_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in reversed(IDENTITY_TABLES):
        op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def _create_identity_resolvers() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.klm_resolve_signup_verification_workspace(
            p_token_digest text,
            p_delivery_id uuid
        ) RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
            SELECT verification.workspace_id
            FROM public.signup_verifications AS verification
            WHERE ((p_token_digest IS NOT NULL) <> (p_delivery_id IS NOT NULL))
              AND (
                    verification.token_digest = p_token_digest
                    OR EXISTS (
                        SELECT 1
                        FROM public.identity_outbox_messages AS delivery
                        WHERE delivery.id = p_delivery_id
                          AND delivery.related_id = verification.id
                          AND delivery.related_type = 'signup_verification'
                          AND delivery.purpose = 'signup_verification'
                    )
              )
            LIMIT 1
        $function$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.klm_resolve_workspace_invitation_workspace(
            p_invitation_id uuid,
            p_token_digest text,
            p_delivery_id uuid
        ) RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
            SELECT invitation.workspace_id
            FROM public.workspace_invitations AS invitation
            WHERE invitation.id = p_invitation_id
              AND ((p_token_digest IS NOT NULL) <> (p_delivery_id IS NOT NULL))
              AND (
                    invitation.token_digest = p_token_digest
                    OR EXISTS (
                        SELECT 1
                        FROM public.identity_outbox_messages AS delivery
                        WHERE delivery.id = p_delivery_id
                          AND delivery.related_id = invitation.id
                          AND delivery.related_type = 'workspace_invitation'
                          AND delivery.purpose = 'workspace_invitation'
                    )
              )
            LIMIT 1
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{SIGNUP_RESOLVER} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION public.{INVITATION_RESOLVER} FROM PUBLIC")
    op.execute(
        f"""
        DO $grant_runtime$
        DECLARE
            runtime_role name;
        BEGIN
            FOR runtime_role IN
                SELECT DISTINCT role.rolname
                FROM pg_catalog.pg_roles AS role
                WHERE role.rolcanlogin
                  AND NOT role.rolsuper
                  AND NOT role.rolbypassrls
                  AND has_table_privilege(
                      role.rolname,
                      'public.identity_outbox_messages',
                      'SELECT'
                  )
                  AND has_table_privilege(
                      role.rolname,
                      'public.signup_verifications',
                      'SELECT'
                  )
                  AND has_table_privilege(
                      role.rolname,
                      'public.workspace_invitations',
                      'SELECT'
                  )
            LOOP
                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION public.{SIGNUP_RESOLVER} TO %I',
                    runtime_role
                );
                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION public.{INVITATION_RESOLVER} TO %I',
                    runtime_role
                );
            END LOOP;
        END
        $grant_runtime$
        """
    )


def _drop_identity_resolvers() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION IF EXISTS public.{INVITATION_RESOLVER}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{SIGNUP_RESOLVER}")


def upgrade() -> None:
    _require_upgrade_consistency()

    op.add_column(
        "users",
        sa.Column(
            "auth_epoch",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    _add_user_email_constraint()
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
        sqlite_where=sa.text("email IS NOT NULL"),
    )
    op.add_column(
        "identity_outbox_messages",
        sa.Column("request_id", FlexibleUUID(), nullable=True),
    )
    op.create_index(
        "ix_identity_outbox_request",
        "identity_outbox_messages",
        ["request_id"],
    )
    invitation_attempts_column = sa.Column(
        "failed_attempt_count",
        sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )
    invitation_lock_column = sa.Column(
        "locked_at",
        sa.DateTime(timezone=True),
        nullable=True,
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "workspace_invitations",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.add_column(invitation_attempts_column)
            batch_op.add_column(invitation_lock_column)
            batch_op.create_check_constraint(
                "ck_workspace_invitation_failed_attempt_count_nonnegative",
                "failed_attempt_count >= 0",
            )
    else:
        op.add_column("workspace_invitations", invitation_attempts_column)
        op.add_column("workspace_invitations", invitation_lock_column)
        op.create_check_constraint(
            "ck_workspace_invitation_failed_attempt_count_nonnegative",
            "workspace_invitations",
            "failed_attempt_count >= 0",
        )
    _add_identity_constraints()

    op.create_table(
        "auth_revocation_intents",
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("auth_epoch", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=False),
        sa.Column("status", auth_revocation_intent_status_enum, nullable=False),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error_type", sa.String(length=160), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_auth_revocation_attempt_nonnegative",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "auth_epoch",
            name="uq_auth_revocation_intent_user_epoch",
        ),
    )
    op.create_index(
        "ix_auth_revocation_intent_retry",
        "auth_revocation_intents",
        ["status", "next_attempt_at"],
    )

    _enable_identity_rls()
    _create_identity_resolvers()


def downgrade() -> None:
    _require_downgrade_safe()
    _drop_identity_resolvers()
    _disable_identity_rls()

    op.drop_index(
        "ix_auth_revocation_intent_retry",
        table_name="auth_revocation_intents",
    )
    op.drop_table("auth_revocation_intents")
    _drop_identity_constraints()

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "workspace_invitations",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "ck_workspace_invitation_failed_attempt_count_nonnegative",
                type_="check",
            )
            batch_op.drop_column("locked_at")
            batch_op.drop_column("failed_attempt_count")
    else:
        op.drop_constraint(
            "ck_workspace_invitation_failed_attempt_count_nonnegative",
            "workspace_invitations",
            type_="check",
        )
        op.drop_column("workspace_invitations", "locked_at")
        op.drop_column("workspace_invitations", "failed_attempt_count")
    op.drop_index(
        "ix_identity_outbox_request",
        table_name="identity_outbox_messages",
    )
    op.drop_column("identity_outbox_messages", "request_id")
    op.drop_index("uq_users_email_lower", table_name="users")
    _drop_user_email_constraint()
    op.drop_column("users", "auth_epoch")
