"""Enforce SaaS P1 tenant references and the complete entitlement catalog.

Revision ID: a9d2e4f6b508
Revises: f8c1d2e3a407
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9d2e4f6b508"
down_revision: str | Sequence[str] | None = "f8c1d2e3a407"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ENTITLEMENT_VALUES = (
    "tokens",
    "ai_executions",
    "ai_conversations",
    "storage_bytes",
    "files",
    "users",
    "whatsapp_numbers",
    "agents",
    "automations",
)
ENTITLEMENT_VALUES = (
    "tokens",
    "ai_executions",
    "ai_conversations",
    "audio_minutes",
    "storage_bytes",
    "files",
    "users",
    "whatsapp_numbers",
    "agents",
    "automations",
    "digital_signature",
    "calendar",
    "email",
    "webhooks",
    "advanced_audit",
    "success_manager",
)
ENTITLEMENT_TABLES = (
    "plan_entitlements",
    "workspace_monthly_entitlement_usage",
    "entitlement_usage_reservations",
)
SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _entitlement_check(values: tuple[str, ...]) -> str:
    return f"entitlement IN ({_quoted(values)})"


def _cross_tenant_violations() -> dict[str, int]:
    bind = op.get_bind()
    checks = {
        "matters.client_id": """
            SELECT COUNT(*)
            FROM matters AS matter
            JOIN clients AS client ON client.id = matter.client_id
            WHERE matter.workspace_id <> client.workspace_id
        """,
        "storage_objects.matter_id": """
            SELECT COUNT(*)
            FROM storage_objects AS object
            JOIN matters AS matter ON matter.id = object.matter_id
            WHERE object.matter_id IS NOT NULL
              AND object.workspace_id <> matter.workspace_id
        """,
        "storage_objects.client_id": """
            SELECT COUNT(*)
            FROM storage_objects AS object
            JOIN clients AS client ON client.id = object.client_id
            WHERE object.client_id IS NOT NULL
              AND object.workspace_id <> client.workspace_id
        """,
    }
    return {
        label: int(bind.execute(sa.text(statement)).scalar_one())
        for label, statement in checks.items()
    }


def _require_tenant_consistency() -> None:
    violations = {
        label: count
        for label, count in _cross_tenant_violations().items()
        if count > 0
    }
    if violations:
        details = ", ".join(
            f"{label}={count}" for label, count in sorted(violations.items())
        )
        raise RuntimeError(
            "SaaS P1 tenant-integrity migration blocked by cross-workspace rows: "
            f"{details}"
        )


def _require_legacy_entitlement_values() -> None:
    bind = op.get_bind()
    allowed = _quoted(LEGACY_ENTITLEMENT_VALUES)
    violations = {
        table_name: int(
            bind.execute(
                sa.text(
                    f'SELECT COUNT(*) FROM "{table_name}" '
                    f"WHERE entitlement NOT IN ({allowed})"
                )
            ).scalar_one()
        )
        for table_name in ENTITLEMENT_TABLES
    }
    violations = {name: count for name, count in violations.items() if count > 0}
    if violations:
        details = ", ".join(
            f"{table_name}={count}"
            for table_name, count in sorted(violations.items())
        )
        raise RuntimeError(
            "Cannot downgrade entitlement catalog while new values are persisted: "
            f"{details}"
        )


def _foreign_key_name(
    table_name: str,
    constrained_columns: tuple[str, ...],
    referred_table: str,
) -> str:
    inspector = sa.inspect(op.get_bind())
    expected_columns = list(constrained_columns)
    for foreign_key in inspector.get_foreign_keys(table_name):
        if (
            foreign_key["constrained_columns"] == expected_columns
            and foreign_key["referred_table"] == referred_table
        ):
            name = foreign_key.get("name")
            if name:
                return str(name)
    raise RuntimeError(
        f"Expected foreign key not found: {table_name}{constrained_columns} -> "
        f"{referred_table}"
    )


def _replace_entitlement_checks(values: tuple[str, ...]) -> None:
    expression = _entitlement_check(values)
    if op.get_bind().dialect.name == "sqlite":
        for table_name in ENTITLEMENT_TABLES:
            with op.batch_alter_table(
                table_name,
                naming_convention=SQLITE_NAMING_CONVENTION,
                recreate="always",
            ) as batch_op:
                batch_op.drop_constraint("entitlement_key_enum", type_="check")
                batch_op.create_check_constraint(
                    "entitlement_key_enum",
                    expression,
                )
        return

    for table_name in ENTITLEMENT_TABLES:
        op.drop_constraint(
            "entitlement_key_enum",
            table_name,
            type_="check",
        )
        op.create_check_constraint(
            "entitlement_key_enum",
            table_name,
            expression,
        )


def _upgrade_tenant_foreign_keys() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "clients",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.create_unique_constraint(
                "uq_clients_workspace_id",
                ["workspace_id", "id"],
            )
        with op.batch_alter_table(
            "matters",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_matters_client_id_clients",
                type_="foreignkey",
            )
            batch_op.create_unique_constraint(
                "uq_matters_workspace_id",
                ["workspace_id", "id"],
            )
            batch_op.create_foreign_key(
                "fk_matters_workspace_client",
                "clients",
                ["workspace_id", "client_id"],
                ["workspace_id", "id"],
                ondelete="RESTRICT",
            )
        with op.batch_alter_table(
            "storage_objects",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_storage_objects_matter_id_matters",
                type_="foreignkey",
            )
            batch_op.drop_constraint(
                "fk_storage_objects_client_id_clients",
                type_="foreignkey",
            )
            batch_op.create_foreign_key(
                "fk_storage_objects_workspace_matter",
                "matters",
                ["workspace_id", "matter_id"],
                ["workspace_id", "id"],
                ondelete="RESTRICT",
            )
            batch_op.create_foreign_key(
                "fk_storage_objects_workspace_client",
                "clients",
                ["workspace_id", "client_id"],
                ["workspace_id", "id"],
                ondelete="RESTRICT",
            )
        return

    op.create_unique_constraint(
        "uq_clients_workspace_id",
        "clients",
        ["workspace_id", "id"],
    )
    op.drop_constraint(
        _foreign_key_name("matters", ("client_id",), "clients"),
        "matters",
        type_="foreignkey",
    )
    op.create_unique_constraint(
        "uq_matters_workspace_id",
        "matters",
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_matters_workspace_client",
        "matters",
        "clients",
        ["workspace_id", "client_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        _foreign_key_name("storage_objects", ("matter_id",), "matters"),
        "storage_objects",
        type_="foreignkey",
    )
    op.drop_constraint(
        _foreign_key_name("storage_objects", ("client_id",), "clients"),
        "storage_objects",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_storage_objects_workspace_matter",
        "storage_objects",
        "matters",
        ["workspace_id", "matter_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_storage_objects_workspace_client",
        "storage_objects",
        "clients",
        ["workspace_id", "client_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )


def _downgrade_tenant_foreign_keys() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "storage_objects",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_storage_objects_workspace_matter",
                type_="foreignkey",
            )
            batch_op.drop_constraint(
                "fk_storage_objects_workspace_client",
                type_="foreignkey",
            )
            batch_op.create_foreign_key(
                "fk_storage_objects_matter_id_matters",
                "matters",
                ["matter_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_foreign_key(
                "fk_storage_objects_client_id_clients",
                "clients",
                ["client_id"],
                ["id"],
                ondelete="RESTRICT",
            )
        with op.batch_alter_table(
            "matters",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_matters_workspace_client",
                type_="foreignkey",
            )
            batch_op.create_foreign_key(
                "fk_matters_client_id_clients",
                "clients",
                ["client_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.drop_constraint(
                "uq_matters_workspace_id",
                type_="unique",
            )
        with op.batch_alter_table(
            "clients",
            naming_convention=SQLITE_NAMING_CONVENTION,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                "uq_clients_workspace_id",
                type_="unique",
            )
        return

    op.drop_constraint(
        "fk_storage_objects_workspace_matter",
        "storage_objects",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_storage_objects_workspace_client",
        "storage_objects",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_storage_objects_matter_id_matters",
        "storage_objects",
        "matters",
        ["matter_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_storage_objects_client_id_clients",
        "storage_objects",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "fk_matters_workspace_client",
        "matters",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_matters_client_id_clients",
        "matters",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "uq_matters_workspace_id",
        "matters",
        type_="unique",
    )
    op.drop_constraint(
        "uq_clients_workspace_id",
        "clients",
        type_="unique",
    )


def upgrade() -> None:
    _require_tenant_consistency()
    _replace_entitlement_checks(ENTITLEMENT_VALUES)
    _upgrade_tenant_foreign_keys()


def downgrade() -> None:
    _require_legacy_entitlement_values()
    _downgrade_tenant_foreign_keys()
    _replace_entitlement_checks(LEGACY_ENTITLEMENT_VALUES)
