"""add canonical contact stock entitlement

Revision ID: e7b9d1f3a526
Revises: d6a8c0e2f415
Create Date: 2026-07-31 08:15:00.000000

The commercial catalog now exposes a finite ``contacts`` stock entitlement.
This forward migration expands every persisted entitlement CHECK without
editing the already-applied catalog migrations.  Downgrade is fail-closed:
it refuses to contract the catalog while any durable contact-entitlement
state exists.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7b9d1f3a526"
down_revision: str | Sequence[str] | None = "d6a8c0e2f415"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS_ENTITLEMENT_VALUES = (
    "tokens",
    "ai_executions",
    "ai_conversations",
    "audio_minutes",
    "storage_bytes",
    "files",
    "users",
    "workspaces",
    "whatsapp_numbers",
    "agents",
    "legal_suite",
    "legal_areas",
    "legal_cases",
    "djen_terms",
    "legal_ai_generations",
    "legal_documents",
    "legal_automations",
    "legal_templates",
    "legal_signatures",
    "protocol_folders",
    "legal_bulk_operations",
    "legal_audit",
    "legal_integrations",
    "automations",
    "active_tasks",
    "task_participants",
    "task_reviews",
    "review_rounds",
    "task_reminder_rules",
    "meeting_participants",
    "digital_signature",
    "calendar",
    "email",
    "webhooks",
    "advanced_audit",
    "success_manager",
    "monitored_processes",
    "campaign_recipients",
)
ENTITLEMENT_VALUES = (
    *PREVIOUS_ENTITLEMENT_VALUES[:3],
    "campaign_recipients",
    "contacts",
    *PREVIOUS_ENTITLEMENT_VALUES[3:-1],
)
PREVIOUS_ENTITLEMENT_LENGTH = max(map(len, PREVIOUS_ENTITLEMENT_VALUES))
ENTITLEMENT_LENGTH = max(map(len, ENTITLEMENT_VALUES))
ENTITLEMENT_CATALOG_TABLES = {
    "plan_entitlements": "entitlement_key_enum",
    "workspace_monthly_entitlement_usage": "entitlement_key_enum",
    "entitlement_usage_reservations": "entitlement_key_enum",
    "workspace_entitlement_overrides": "entitlement_key_enum",
    "entitlement_bonuses": "commercial_bonus_entitlement_enum",
}
SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _entitlement_check(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"entitlement IN ({allowed})"


def _constraint_entitlement_values(
    table_name: str,
    constraint_name: str,
) -> frozenset[str]:
    checks = sa.inspect(op.get_bind()).get_check_constraints(table_name)
    matches = [check for check in checks if check.get("name") == constraint_name]
    if len(matches) != 1:
        raise RuntimeError(
            "Entitlement catalog drift blocks migration for "
            f"{table_name}; expected exactly one {constraint_name!r} CHECK, "
            f"found {len(matches)}"
        )
    sqltext = str(matches[0].get("sqltext") or "")
    return frozenset(re.findall(r"'([^']+)'", sqltext))


def _require_entitlement_catalog(values: tuple[str, ...]) -> None:
    expected = frozenset(values)
    for table_name, constraint_name in ENTITLEMENT_CATALOG_TABLES.items():
        actual = _constraint_entitlement_values(table_name, constraint_name)
        if actual != expected:
            raise RuntimeError(
                "Entitlement catalog drift blocks migration for "
                f"{table_name}; expected={sorted(expected)!r}, "
                f"actual={sorted(actual)!r}"
            )


def _replace_entitlement_catalog(
    values: tuple[str, ...],
    *,
    previous_values: tuple[str, ...],
    previous_length: int,
    target_length: int,
) -> None:
    """Replace every persisted non-native enum CHECK without hiding drift."""

    _require_entitlement_catalog(previous_values)
    expression = _entitlement_check(values)
    if op.get_bind().dialect.name == "sqlite":
        for table_name, constraint_name in ENTITLEMENT_CATALOG_TABLES.items():
            with op.batch_alter_table(
                table_name,
                naming_convention=SQLITE_NAMING_CONVENTION,
                recreate="always",
            ) as batch_op:
                batch_op.drop_constraint(constraint_name, type_="check")
                batch_op.alter_column(
                    "entitlement",
                    existing_type=sa.String(length=previous_length),
                    type_=sa.String(length=target_length),
                    existing_nullable=False,
                )
                batch_op.create_check_constraint(constraint_name, expression)
    else:
        for table_name, constraint_name in ENTITLEMENT_CATALOG_TABLES.items():
            op.drop_constraint(constraint_name, table_name, type_="check")
            op.alter_column(
                table_name,
                "entitlement",
                existing_type=sa.String(length=previous_length),
                type_=sa.String(length=target_length),
                existing_nullable=False,
            )
            op.create_check_constraint(
                constraint_name,
                table_name,
                expression,
            )
    _require_entitlement_catalog(values)


def _require_no_contact_entitlement_state_before_downgrade() -> None:
    """Refuse to remove a catalog key that already carries durable state."""

    bind = op.get_bind()
    violations = {
        table_name: int(
            bind.execute(
                sa.text(
                    f'SELECT COUNT(*) FROM "{table_name}" '
                    "WHERE entitlement = :entitlement"
                ),
                {"entitlement": "contacts"},
            ).scalar_one()
        )
        for table_name in ENTITLEMENT_CATALOG_TABLES
    }
    violations = {name: count for name, count in violations.items() if count > 0}
    if violations:
        detail = ", ".join(
            f"{table_name}={count}"
            for table_name, count in sorted(violations.items())
        )
        raise RuntimeError(
            "Cannot downgrade contacts while durable entitlement state is "
            f"persisted: {detail}"
        )


def upgrade() -> None:
    _replace_entitlement_catalog(
        ENTITLEMENT_VALUES,
        previous_values=PREVIOUS_ENTITLEMENT_VALUES,
        previous_length=PREVIOUS_ENTITLEMENT_LENGTH,
        target_length=ENTITLEMENT_LENGTH,
    )


def downgrade() -> None:
    _require_no_contact_entitlement_state_before_downgrade()
    _replace_entitlement_catalog(
        PREVIOUS_ENTITLEMENT_VALUES,
        previous_values=ENTITLEMENT_VALUES,
        previous_length=ENTITLEMENT_LENGTH,
        target_length=PREVIOUS_ENTITLEMENT_LENGTH,
    )
