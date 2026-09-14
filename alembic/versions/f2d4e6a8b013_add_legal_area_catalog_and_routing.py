"""Persist the extensible legal-area catalog and routing context.

Revision ID: f2d4e6a8b013
Revises: f1c3e5a7b902
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON

revision: str = "f2d4e6a8b013"
down_revision: str | Sequence[str] | None = "f1c3e5a7b902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRACTICE_AREA_VALUES = (
    "erro_medico",
    "bancario",
    "previdenciario",
    "consumidor",
    "digital",
    "propriedade_intelectual",
    "seguros",
    "familia",
    "imobiliario",
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
    "legal_areas",
)

LEGACY_ENTITLEMENT_VALUES = tuple(
    value for value in ENTITLEMENT_VALUES if value != "legal_areas"
)

ENTITLEMENT_TABLES = (
    "plan_entitlements",
    "workspace_monthly_entitlement_usage",
    "entitlement_usage_reservations",
)

SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _entitlement_check(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"entitlement IN ({allowed})"


def _replace_entitlement_checks(values: tuple[str, ...]) -> None:
    expression = _entitlement_check(values)
    if op.get_bind().dialect.name == "sqlite":
        for table_name in ENTITLEMENT_TABLES:
            with op.batch_alter_table(
                table_name,
                naming_convention=SQLITE_NAMING_CONVENTION,
                recreate="always",
            ) as batch:
                batch.drop_constraint("entitlement_key_enum", type_="check")
                batch.create_check_constraint("entitlement_key_enum", expression)
        return

    for table_name in ENTITLEMENT_TABLES:
        op.drop_constraint("entitlement_key_enum", table_name, type_="check")
        op.create_check_constraint(
            "entitlement_key_enum",
            table_name,
            expression,
        )


def _require_no_legal_area_entitlement_rows() -> None:
    bind = op.get_bind()
    violations = {
        table_name: int(
            bind.execute(
                sa.text(
                    f'SELECT COUNT(*) FROM "{table_name}" '
                    "WHERE entitlement = 'legal_areas'"
                )
            ).scalar_one()
        )
        for table_name in ENTITLEMENT_TABLES
    }
    violations = {name: count for name, count in violations.items() if count}
    if violations:
        detail = ", ".join(
            f"{table_name}={count}" for table_name, count in sorted(violations.items())
        )
        raise RuntimeError(
            "Cannot downgrade while legal-area entitlements are persisted: " + detail
        )


def _extend_practice_area_enum() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for value in PRACTICE_AREA_VALUES:
        op.execute(
            f"ALTER TYPE practice_area_enum ADD VALUE IF NOT EXISTS '{value}'"
        )


def upgrade() -> None:
    _extend_practice_area_enum()

    with op.batch_alter_table("matters") as batch:
        batch.add_column(sa.Column("legal_area_key", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("legal_product_key", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("legal_product_custom", sa.String(length=180), nullable=True))
        batch.add_column(sa.Column("legal_object_name", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("counterparty_name", sa.String(length=240), nullable=True))
        batch.add_column(sa.Column("legal_issue_summary", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "legal_context",
                FlexibleJSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    op.create_index("ix_matters_legal_area_key", "matters", ["legal_area_key"])
    op.create_index("ix_matters_legal_product_key", "matters", ["legal_product_key"])
    op.create_index("ix_matters_legal_object_name", "matters", ["legal_object_name"])
    op.create_index(
        "ix_matters_workspace_legal_route",
        "matters",
        ["workspace_id", "legal_area_key", "legal_product_key"],
    )

    op.execute(
        sa.text(
            "UPDATE matters SET legal_area_key = CAST(practice_area AS TEXT) "
            "WHERE legal_area_key IS NULL"
        )
    )
    _replace_entitlement_checks(ENTITLEMENT_VALUES)


def downgrade() -> None:
    _require_no_legal_area_entitlement_rows()
    _replace_entitlement_checks(LEGACY_ENTITLEMENT_VALUES)

    op.drop_index("ix_matters_workspace_legal_route", table_name="matters")
    op.drop_index("ix_matters_legal_object_name", table_name="matters")
    op.drop_index("ix_matters_legal_product_key", table_name="matters")
    op.drop_index("ix_matters_legal_area_key", table_name="matters")
    with op.batch_alter_table("matters") as batch:
        batch.drop_column("legal_context")
        batch.drop_column("legal_issue_summary")
        batch.drop_column("counterparty_name")
        batch.drop_column("legal_object_name")
        batch.drop_column("legal_product_custom")
        batch.drop_column("legal_product_key")
        batch.drop_column("legal_area_key")

    # PostgreSQL enum labels are intentionally retained. Removing labels that may
    # be referenced by audit history is destructive and unsupported by ALTER TYPE.
