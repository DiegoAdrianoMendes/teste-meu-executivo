"""Add the V1 collaboration and canonical commercial control plane.

Revision ID: c8e0f2a4b615
Revises: b71d0e4c9f52
Create Date: 2026-07-30

This is an expand-only release migration. It preserves every legacy task,
meeting and subscription field so the previous application image can still run
against the expanded schema during rollback. No previously published revision
is edited.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c8e0f2a4b615"
down_revision: str | Sequence[str] | None = "b71d0e4c9f52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
PLATFORM_SCOPE_SETTING = "app.current_platform_scope"
PLATFORM_ACTOR_SETTING = "app.current_platform_actor_id"
PLATFORM_ACTION_SETTING = "app.current_platform_action"
RLS_POLICY = "workspace_current_id"
COMMERCIAL_TENANT_READ_POLICY = "workspace_current_id_select"
COMMERCIAL_PLATFORM_READ_POLICY = "platform_commercial_read"
COMMERCIAL_PLATFORM_INSERT_POLICY = "platform_commercial_insert"
COMMERCIAL_PLATFORM_UPDATE_POLICY = "platform_commercial_update"
PLATFORM_TASK_SELECT_POLICY = "platform_task_select"
PLATFORM_TASK_INSERT_POLICY = "platform_task_insert"
PLATFORM_TASK_UPDATE_POLICY = "platform_task_update"

COMMERCIAL_TABLES = (
    "commercial_adjustments",
    "commercial_catalog_events",
    "commercial_command_receipts",
    "commercial_offer_versions",
    "commercial_offers",
    "entitlement_bonuses",
    "workspace_commercial_contracts",
    "workspace_contract_lines",
)
PLATFORM_TASK_TABLES = (
    "platform_task_events",
    "platform_task_participants",
    "platform_task_review_rounds",
    "platform_tasks",
)
TASK_REMINDER_TABLES = (
    "task_reminder_rules",
    "task_reminder_outbox",
    "task_reminder_events",
    "task_reminder_command_receipts",
)
WORKSPACE_COLLABORATION_TABLES = (
    "meeting_creation_receipts",
    "meeting_notification_outbox",
    "meeting_participants",
    "task_events",
    "task_participants",
    "task_review_rounds",
    *TASK_REMINDER_TABLES,
)
IMMUTABLE_TABLES = (
    "commercial_catalog_events",
    "commercial_command_receipts",
    "commercial_offer_versions",
    "commercial_offers",
    "meeting_creation_receipts",
    "platform_task_events",
    "task_events",
    "task_reminder_command_receipts",
    "task_reminder_events",
)
NON_RECONSTRUCTIBLE_V1_TABLES = tuple(
    table_name
    for table_name in (
        *COMMERCIAL_TABLES,
        *PLATFORM_TASK_TABLES,
        *WORKSPACE_COLLABORATION_TABLES,
    )
    if table_name not in {"task_participants", "meeting_participants"}
)

# The entitlement columns are SQLAlchemy non-native enums.  PostgreSQL stores
# those as VARCHAR plus a named CHECK constraint, so adding enum members must
# expand both the constraint *and* the physical column length.  Alembic's
# metadata comparison does not reliably report this drift for non-native enums.
LEGACY_ENTITLEMENT_VALUES = (
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
    "monitored_processes",
)
ENTITLEMENT_VALUES = (
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
)
ENTITLEMENT_TABLES = (
    "plan_entitlements",
    "workspace_monthly_entitlement_usage",
    "entitlement_usage_reservations",
    "workspace_entitlement_overrides",
)
LEGACY_ENTITLEMENT_LENGTH = max(map(len, LEGACY_ENTITLEMENT_VALUES))
ENTITLEMENT_LENGTH = max(map(len, ENTITLEMENT_VALUES))
SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _entitlement_check(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"entitlement IN ({allowed})"


def _replace_entitlement_catalog(
    values: tuple[str, ...],
    *,
    previous_length: int,
    target_length: int,
) -> None:
    """Replace the legacy enum CHECK and its VARCHAR width together."""

    expression = _entitlement_check(values)
    if op.get_bind().dialect.name == "sqlite":
        for table_name in ENTITLEMENT_TABLES:
            check_names = {
                check.get("name")
                for check in sa.inspect(op.get_bind()).get_check_constraints(table_name)
            }
            with op.batch_alter_table(
                table_name,
                naming_convention=SQLITE_NAMING_CONVENTION,
                recreate="always",
            ) as batch_op:
                if "entitlement_key_enum" in check_names:
                    batch_op.drop_constraint("entitlement_key_enum", type_="check")
                batch_op.alter_column(
                    "entitlement",
                    existing_type=sa.String(length=previous_length),
                    type_=sa.String(length=target_length),
                    existing_nullable=False,
                )
                batch_op.create_check_constraint(
                    "entitlement_key_enum",
                    expression,
                )
        return

    for table_name in ENTITLEMENT_TABLES:
        op.drop_constraint("entitlement_key_enum", table_name, type_="check")
        op.alter_column(
            table_name,
            "entitlement",
            existing_type=sa.String(length=previous_length),
            type_=sa.String(length=target_length),
            existing_nullable=False,
        )
        op.create_check_constraint(
            "entitlement_key_enum",
            table_name,
            expression,
        )


def _require_legacy_entitlement_values_before_downgrade() -> None:
    """Refuse a lossy schema contraction when new capabilities are in use."""

    allowed = ", ".join(f"'{value}'" for value in LEGACY_ENTITLEMENT_VALUES)
    bind = op.get_bind()
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
        detail = ", ".join(
            f"{table_name}={count}"
            for table_name, count in sorted(violations.items())
        )
        raise RuntimeError(
            "Cannot downgrade the V1 entitlement catalog while new values are "
            f"persisted: {detail}"
        )


def _uuid_type() -> sa.TypeEngine:
    return sa.Uuid().with_variant(sa.CHAR(length=36), "sqlite")


def _json_type() -> sa.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _enum(name: str, *values: str, length: int | None = None) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
    )


def _platform_predicate(*capabilities: tuple[str, str]) -> str:
    allowed = " OR ".join(
        "("
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = '{module}' "
        f"AND current_setting('{PLATFORM_ACTION_SETTING}', true) = '{module}:{action}'"
        ")"
        for module, action in capabilities
    )
    return (
        f"NULLIF(current_setting('{PLATFORM_ACTOR_SETTING}', true), '') IS NOT NULL "
        "AND ("
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = 'platform_root'"
        + (f" OR {allowed}" if allowed else "")
        + ")"
    )


COMMERCIAL_TENANT_READ_TABLES = (
    "commercial_adjustments",
    "commercial_offer_versions",
    "commercial_offers",
    "entitlement_bonuses",
    "workspace_commercial_contracts",
    "workspace_contract_lines",
)
COMMERCIAL_PLATFORM_ONLY_TABLES = tuple(
    table_name
    for table_name in COMMERCIAL_TABLES
    if table_name not in COMMERCIAL_TENANT_READ_TABLES
)

_COMMERCIAL_READ_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "commercial_offers": (
        ("plans", "view"),
        ("plans", "manage"),
        ("plans", "draft"),
        ("plans", "preview"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
    ),
    "commercial_offer_versions": (
        ("plans", "view"),
        ("plans", "manage"),
        ("plans", "draft"),
        ("plans", "preview"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
    ),
    "workspace_commercial_contracts": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "view"),
        ("commercial_adjustments", "manage"),
    ),
    "workspace_contract_lines": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
    ),
    "commercial_adjustments": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "view"),
        ("commercial_adjustments", "manage"),
    ),
    "entitlement_bonuses": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("entitlement_bonuses", "view"),
        ("entitlement_bonuses", "manage"),
    ),
    "commercial_command_receipts": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
    ),
    "commercial_catalog_events": (
        ("plans", "view"),
        ("plans", "draft"),
        ("plans", "preview"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
    ),
}

_COMMERCIAL_WRITE_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "commercial_offers": (),
    "commercial_offer_versions": (),
    "workspace_commercial_contracts": (("commercial_contracts", "manage"),),
    "workspace_contract_lines": (("commercial_contracts", "manage"),),
    "commercial_adjustments": (
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
    ),
    "entitlement_bonuses": (
        ("commercial_contracts", "manage"),
        ("entitlement_bonuses", "manage"),
    ),
    "commercial_command_receipts": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
    ),
    "commercial_catalog_events": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
    ),
}


def _commercial_tenant_read_predicate(table_name: str) -> str:
    workspace = f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    if table_name == "workspace_commercial_contracts":
        tenant = f"workspace_id::text = {workspace}"
    elif table_name == "workspace_contract_lines":
        tenant = (
            "EXISTS (SELECT 1 FROM workspace_commercial_contracts "
            "WHERE workspace_commercial_contracts.id = "
            "workspace_contract_lines.contract_id "
            f"AND workspace_commercial_contracts.workspace_id::text = {workspace})"
        )
    elif table_name == "commercial_adjustments":
        tenant = (
            "EXISTS (SELECT 1 FROM workspace_commercial_contracts "
            "WHERE workspace_commercial_contracts.id = commercial_adjustments.contract_id "
            f"AND workspace_commercial_contracts.workspace_id::text = {workspace})"
        )
    elif table_name == "entitlement_bonuses":
        tenant = f"workspace_id::text = {workspace}"
    elif table_name == "commercial_offers":
        tenant = (
            "(EXISTS (SELECT 1 FROM commercial_offer_versions "
            "WHERE commercial_offer_versions.offer_id = commercial_offers.id "
            "AND commercial_offer_versions.is_public = true "
            "AND commercial_offer_versions.is_active = true "
            f"AND {workspace} IS NOT NULL)) OR EXISTS ("
            "SELECT 1 FROM workspace_contract_lines "
            "JOIN workspace_commercial_contracts ON "
            "workspace_commercial_contracts.id = workspace_contract_lines.contract_id "
            "WHERE workspace_contract_lines.offer_id = commercial_offers.id "
            f"AND workspace_commercial_contracts.workspace_id::text = {workspace})"
        )
    elif table_name == "commercial_offer_versions":
        tenant = (
            "(is_public = true AND is_active = true "
            f"AND {workspace} IS NOT NULL) OR EXISTS ("
            "SELECT 1 FROM workspace_contract_lines "
            "JOIN workspace_commercial_contracts ON "
            "workspace_commercial_contracts.id = workspace_contract_lines.contract_id "
            "WHERE workspace_contract_lines.policy_version_id = commercial_offer_versions.id "
            f"AND workspace_commercial_contracts.workspace_id::text = {workspace})"
        )
    else:
        raise KeyError(f"No tenant commercial predicate for {table_name}")
    return tenant


def _commercial_read_predicate(table_name: str) -> str:
    return _platform_predicate(*_COMMERCIAL_READ_CAPABILITIES[table_name])


def _commercial_write_predicate(table_name: str) -> str:
    return _platform_predicate(*_COMMERCIAL_WRITE_CAPABILITIES[table_name])


_PLATFORM_TASK_READ_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "platform_tasks": tuple(
        ("platform_tasks", action)
        for action in ("view", "create", "edit", "assign", "review", "complete", "comment")
    ),
    "platform_task_participants": tuple(
        ("platform_tasks", action)
        for action in ("view", "create", "edit", "assign", "review", "complete", "comment")
    ),
    "platform_task_review_rounds": tuple(
        ("platform_tasks", action)
        for action in ("view", "create", "edit", "assign", "review", "complete", "comment")
    ),
    "platform_task_events": (("platform_tasks", "view"),),
}
_PLATFORM_TASK_INSERT_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "platform_tasks": (("platform_tasks", "create"),),
    "platform_task_participants": (
        ("platform_tasks", "create"),
        ("platform_tasks", "assign"),
    ),
    "platform_task_review_rounds": (("platform_tasks", "edit"),),
    "platform_task_events": tuple(
        ("platform_tasks", action)
        for action in ("create", "edit", "assign", "review", "complete", "comment")
    ),
}
_PLATFORM_TASK_UPDATE_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "platform_tasks": tuple(
        ("platform_tasks", action) for action in ("edit", "assign", "review", "complete")
    ),
    "platform_task_participants": (
        ("platform_tasks", "edit"),
        ("platform_tasks", "assign"),
    ),
    "platform_task_review_rounds": (
        ("platform_tasks", "review"),
        ("platform_tasks", "complete"),
    ),
}


def _enable_rls_table(table_name: str) -> None:
    if not _is_postgres():
        return
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')


def _create_rls_policy(
    table_name: str,
    *,
    name: str,
    command: str,
    using: str | None,
    with_check: str | None,
) -> None:
    if not _is_postgres():
        return
    clauses = [f'CREATE POLICY "{name}" ON "{table_name}"', f"FOR {command}"]
    if using is not None:
        clauses.append(f"USING ({using})")
    if with_check is not None:
        clauses.append(f"WITH CHECK ({with_check})")
    op.execute("\n".join(clauses))


def _enable_commercial_rls(table_name: str) -> None:
    _enable_rls_table(table_name)
    if table_name in COMMERCIAL_TENANT_READ_TABLES:
        _create_rls_policy(
            table_name,
            name=COMMERCIAL_TENANT_READ_POLICY,
            command="SELECT",
            using=_commercial_tenant_read_predicate(table_name),
            with_check=None,
        )
    _create_rls_policy(
        table_name,
        name=COMMERCIAL_PLATFORM_READ_POLICY,
        command="SELECT",
        using=_commercial_read_predicate(table_name),
        with_check=None,
    )
    _create_rls_policy(
        table_name,
        name=COMMERCIAL_PLATFORM_INSERT_POLICY,
        command="INSERT",
        using=None,
        with_check=_commercial_write_predicate(table_name),
    )
    if table_name in {
        "commercial_adjustments",
        "entitlement_bonuses",
        "workspace_commercial_contracts",
        "workspace_contract_lines",
    }:
        predicate = _commercial_write_predicate(table_name)
        _create_rls_policy(
            table_name,
            name=COMMERCIAL_PLATFORM_UPDATE_POLICY,
            command="UPDATE",
            using=predicate,
            with_check=predicate,
        )


def _enable_platform_task_rls(table_name: str) -> None:
    _enable_rls_table(table_name)
    _create_rls_policy(
        table_name,
        name=PLATFORM_TASK_SELECT_POLICY,
        command="SELECT",
        using=_platform_predicate(*_PLATFORM_TASK_READ_CAPABILITIES[table_name]),
        with_check=None,
    )
    _create_rls_policy(
        table_name,
        name=PLATFORM_TASK_INSERT_POLICY,
        command="INSERT",
        using=None,
        with_check=_platform_predicate(*_PLATFORM_TASK_INSERT_CAPABILITIES[table_name]),
    )
    if table_name in _PLATFORM_TASK_UPDATE_CAPABILITIES:
        predicate = _platform_predicate(*_PLATFORM_TASK_UPDATE_CAPABILITIES[table_name])
        _create_rls_policy(
            table_name,
            name=PLATFORM_TASK_UPDATE_POLICY,
            command="UPDATE",
            using=predicate,
            with_check=predicate,
        )


def _enable_rls(table_name: str, predicate: str) -> None:
    if not _is_postgres():
        return
    _enable_rls_table(table_name)
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            FOR ALL
            USING ({predicate})
            WITH CHECK ({predicate})'''
    )


def _drop_rls(table_name: str) -> None:
    if not _is_postgres():
        return
    for policy_name in (
        RLS_POLICY,
        COMMERCIAL_TENANT_READ_POLICY,
        COMMERCIAL_PLATFORM_READ_POLICY,
        COMMERCIAL_PLATFORM_INSERT_POLICY,
        COMMERCIAL_PLATFORM_UPDATE_POLICY,
        PLATFORM_TASK_SELECT_POLICY,
        PLATFORM_TASK_INSERT_POLICY,
        PLATFORM_TASK_UPDATE_POLICY,
    ):
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
    op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def _install_append_only(table_name: str) -> None:
    if not _is_postgres():
        op.execute(
            f'''CREATE TRIGGER "trg_{table_name}_immutable_update"
                BEFORE UPDATE ON "{table_name}"
                BEGIN
                    SELECT RAISE(ABORT, '{table_name} is immutable');
                END'''
        )
        op.execute(
            f'''CREATE TRIGGER "trg_{table_name}_immutable_delete"
                BEFORE DELETE ON "{table_name}"
                BEGIN
                    SELECT RAISE(ABORT, '{table_name} is immutable');
                END'''
        )
        return
    function_name = f"klm_reject_{table_name}_mutation"
    op.execute(
        f'''CREATE OR REPLACE FUNCTION "{function_name}"()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION '{table_name} is immutable';
            END;
            $$'''
    )
    op.execute(
        f'''CREATE TRIGGER "trg_{table_name}_immutable"
            BEFORE UPDATE OR DELETE ON "{table_name}"
            FOR EACH ROW EXECUTE FUNCTION "{function_name}"()'''
    )
    op.execute(
        f'''CREATE TRIGGER "trg_{table_name}_immutable_truncate"
            BEFORE TRUNCATE ON "{table_name}"
            FOR EACH STATEMENT EXECUTE FUNCTION "{function_name}"()'''
    )


def _drop_append_only(table_name: str) -> None:
    if not _is_postgres():
        op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_delete"')
        op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_update"')
        return
    function_name = f"klm_reject_{table_name}_mutation"
    op.execute(
        f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_truncate" ON "{table_name}"'
    )
    op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable" ON "{table_name}"')
    op.execute(f'DROP FUNCTION IF EXISTS "{function_name}"()')


def _create_commercial_tables() -> None:
    uuid_type = _uuid_type()
    json_type = _json_type()
    op.create_table(
        "commercial_catalog_events",
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("actor_user_id", uuid_type, nullable=True),
        sa.Column("workspace_id", uuid_type, nullable=True),
        sa.Column("resource_type", sa.String(length=60), nullable=False),
        sa.Column("resource_id", uuid_type, nullable=True),
        sa.Column("before", json_type, nullable=True),
        sa.Column("after", json_type, nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(length=200), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_commercial_event_correlation",
        "commercial_catalog_events",
        ["correlation_id"],
    )
    op.create_index(
        "ix_commercial_event_resource",
        "commercial_catalog_events",
        ["resource_type", "resource_id"],
    )
    op.create_index(
        "ix_commercial_event_workspace",
        "commercial_catalog_events",
        ["workspace_id", "occurred_at"],
    )

    op.create_table(
        "commercial_command_receipts",
        sa.Column("operation", sa.String(length=60), nullable=False),
        sa.Column("scope_key", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_payload", json_type, nullable=False),
        sa.Column("actor_user_id", uuid_type, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation",
            "scope_key",
            "idempotency_key",
            name="uq_commercial_command_idempotency",
        ),
    )
    op.create_index(
        "ix_commercial_command_receipt_created",
        "commercial_command_receipts",
        ["created_at"],
    )

    op.create_table(
        "commercial_offers",
        sa.Column("sku", sa.String(length=80), nullable=False),
        sa.Column(
            "family",
            _enum("commercial_offer_family_enum", "crm", "legal", "internal"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            _enum("commercial_offer_kind_enum", "base_plan", "solution"),
            nullable=False,
        ),
        sa.Column("legacy_tier", sa.String(length=40), nullable=True),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "family", name="uq_commercial_offer_id_family"),
        sa.UniqueConstraint("sku", name="uq_commercial_offer_sku"),
    )
    op.create_index(
        "ix_commercial_offer_family", "commercial_offers", ["family", "kind"]
    )

    op.create_table(
        "entitlement_bonuses",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column(
            "entitlement",
            _enum("commercial_bonus_entitlement_enum", *ENTITLEMENT_VALUES),
            nullable=False,
        ),
        sa.Column(
            "mode",
            _enum("entitlement_bonus_mode_enum", "quantity_delta", "feature_grant"),
            nullable=False,
        ),
        sa.Column("quantity", sa.BigInteger(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("renews_each_period", sa.Boolean(), nullable=False),
        sa.Column(
            "portability",
            _enum(
                "entitlement_bonus_portability_enum",
                "preserve",
                "expire_on_offer_change",
                "recalculate",
                "manual_review",
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "source",
            _enum(
                "entitlement_bonus_source_enum",
                "manual",
                "benefit",
                "campaign",
                "compensation",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "entitlement_bonus_status_enum",
                "scheduled",
                "active",
                "expired",
                "revoked",
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(mode = 'quantity_delta' AND quantity IS NOT NULL) OR "
            "(mode = 'feature_grant' AND quantity IS NULL)",
            name="ck_bonus_mode_quantity",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_bonus_effective_window",
        ),
        sa.CheckConstraint("length(trim(reason)) >= 3", name="ck_bonus_reason"),
        sa.CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_bonus_quantity"),
        sa.CheckConstraint("version > 0", name="ck_bonus_version_positive"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_entitlement_bonus_workspace_key",
        "entitlement_bonuses",
        ["workspace_id", "entitlement"],
    )
    op.create_index(
        "ix_entitlement_bonus_workspace_status",
        "entitlement_bonuses",
        ["workspace_id", "status"],
    )

    op.create_table(
        "workspace_commercial_contracts",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column(
            "status",
            _enum(
                "commercial_contract_status_enum",
                "draft",
                "trialing",
                "active",
                "past_due",
                "paused",
                "cancelled",
            ),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("updated_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="ck_workspace_contract_version_positive"),
        sa.CheckConstraint(
            "currency = 'BRL'", name="ck_workspace_contract_currency_brl"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_workspace_commercial_contract"),
    )
    op.create_index(
        "ix_workspace_contract_status",
        "workspace_commercial_contracts",
        ["status"],
    )

    op.create_table(
        "commercial_offer_versions",
        sa.Column("offer_id", uuid_type, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_recommended", sa.Boolean(), nullable=False),
        sa.Column("is_quote_only", sa.Boolean(), nullable=False),
        sa.Column("available_intervals", json_type, nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_monthly_cents", sa.BigInteger(), nullable=True),
        sa.Column("price_annual_cents", sa.BigInteger(), nullable=True),
        sa.Column("cta", sa.String(length=120), nullable=False),
        sa.Column("entitlement_policy", json_type, nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("publication_reason", sa.Text(), nullable=False),
        sa.Column("published_by_user_id", uuid_type, nullable=True),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", uuid_type, nullable=False),
        sa.CheckConstraint(
            "length(trim(publication_reason)) >= 3",
            name="ck_commercial_offer_publication_reason",
        ),
        sa.CheckConstraint(
            "price_annual_cents IS NULL OR price_annual_cents >= 0",
            name="ck_commercial_offer_annual_price_nonnegative",
        ),
        sa.CheckConstraint(
            "price_monthly_cents IS NULL OR price_monthly_cents >= 0",
            name="ck_commercial_offer_monthly_price_nonnegative",
        ),
        sa.CheckConstraint("version > 0", name="ck_commercial_offer_version_positive"),
        sa.CheckConstraint(
            "currency = 'BRL'", name="ck_commercial_offer_currency_brl"
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["commercial_offers.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["published_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "offer_id", name="uq_commercial_offer_version_offer_binding"
        ),
        sa.UniqueConstraint(
            "offer_id", "version", name="uq_commercial_offer_version"
        ),
    )
    op.create_index(
        "ix_commercial_offer_version_latest",
        "commercial_offer_versions",
        ["offer_id", "version"],
    )

    op.create_table(
        "workspace_contract_lines",
        sa.Column("contract_id", uuid_type, nullable=False),
        sa.Column("offer_id", uuid_type, nullable=False),
        sa.Column("policy_version_id", uuid_type, nullable=False),
        sa.Column(
            "offer_family",
            _enum("contract_line_offer_family_enum", "crm", "legal", "internal"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("list_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("contracted_price_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "billing_interval",
            _enum("commercial_billing_interval_enum", "monthly", "annual"),
            nullable=False,
        ),
        sa.Column("entitlement_overrides", json_type, nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            _enum(
                "commercial_contract_line_status_enum",
                "scheduled",
                "active",
                "suspended",
                "cancelled",
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("updated_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_workspace_contract_line_effective_window",
        ),
        sa.CheckConstraint(
            "list_price_cents >= 0 AND contracted_price_cents >= 0",
            name="ck_workspace_contract_line_prices_nonnegative",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_workspace_contract_line_quantity"),
        sa.CheckConstraint(
            "version > 0", name="ck_workspace_contract_line_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["workspace_commercial_contracts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["offer_id", "offer_family"],
            ["commercial_offers.id", "commercial_offers.family"],
            name="fk_contract_line_offer_family",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id", "offer_id"],
            ["commercial_offer_versions.id", "commercial_offer_versions.offer_id"],
            name="fk_contract_line_policy_offer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "contract_id", name="uq_workspace_contract_line_contract_binding"
        ),
    )
    op.create_index(
        "ix_workspace_contract_line_contract",
        "workspace_contract_lines",
        ["contract_id", "status"],
    )
    op.create_index(
        "uq_workspace_active_contract_line_family",
        "workspace_contract_lines",
        ["contract_id", "offer_family"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "commercial_adjustments",
        sa.Column("contract_id", uuid_type, nullable=False),
        sa.Column("contract_line_id", uuid_type, nullable=True),
        sa.Column(
            "type",
            _enum(
                "commercial_adjustment_type_enum",
                "percentage_discount",
                "fixed_discount",
                "negotiated_price",
                "courtesy_period",
                "invoice_credit",
            ),
            nullable=False,
        ),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remaining_cycles", sa.Integer(), nullable=True),
        sa.Column("stack_group", sa.String(length=80), nullable=False),
        sa.Column("stackable", sa.Boolean(), nullable=False),
        sa.Column(
            "portability",
            _enum(
                "commercial_adjustment_portability_enum",
                "preserve",
                "expire_on_offer_change",
                "recalculate",
                "manual_review",
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "source",
            _enum(
                "commercial_adjustment_source_enum",
                "manual",
                "coupon",
                "benefit",
                "referral",
                "campaign",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "commercial_adjustment_status_enum",
                "scheduled",
                "active",
                "expired",
                "revoked",
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_commercial_adjustment_window",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) >= 3", name="ck_commercial_adjustment_reason"
        ),
        sa.CheckConstraint(
            "value >= 0", name="ck_commercial_adjustment_value_nonnegative"
        ),
        sa.CheckConstraint(
            "type <> 'percentage_discount' OR value <= 10000",
            name="ck_commercial_adjustment_percentage_bps",
        ),
        sa.CheckConstraint(
            "remaining_cycles IS NULL OR remaining_cycles >= 0",
            name="ck_commercial_adjustment_remaining_cycles_nonnegative",
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_commercial_adjustment_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["workspace_commercial_contracts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["contract_line_id", "contract_id"],
            ["workspace_contract_lines.id", "workspace_contract_lines.contract_id"],
            name="fk_commercial_adjustment_line_contract",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_commercial_adjustment_contract_status",
        "commercial_adjustments",
        ["contract_id", "status"],
    )
    op.create_index(
        "ix_commercial_adjustment_line_status",
        "commercial_adjustments",
        ["contract_line_id", "status"],
    )


def upgrade() -> None:
    _replace_entitlement_catalog(
        ENTITLEMENT_VALUES,
        previous_length=LEGACY_ENTITLEMENT_LENGTH,
        target_length=ENTITLEMENT_LENGTH,
    )
    _expand_private_storage_idempotency()
    _create_commercial_tables()
    # Collaboration tables and backfill are created below in dependency order.
    _create_collaboration_tables()
    _create_task_reminder_tables()
    _create_platform_task_tables()
    for table_name in IMMUTABLE_TABLES:
        _install_append_only(table_name)
    for table_name in COMMERCIAL_TABLES:
        _enable_commercial_rls(table_name)
    workspace_predicate = (
        f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )
    for table_name in WORKSPACE_COLLABORATION_TABLES:
        _enable_rls(table_name, workspace_predicate)
    for table_name in PLATFORM_TASK_TABLES:
        _enable_platform_task_rls(table_name)
    # Expand-only candidate: keep the previous image's explicit runtime ACLs
    # intact throughout bake and rollback. The disposable PostgreSQL QA gate
    # exercises the role-bootstrap reconciliation later, as the contract
    # phase; production contraction requires a separate forward migration in
    # a subsequent release after the rollback window closes.


def downgrade() -> None:
    _require_no_non_reconstructible_v1_data_before_downgrade()
    _require_legacy_entitlement_values_before_downgrade()
    for table_name in (
        *COMMERCIAL_TABLES,
        *PLATFORM_TASK_TABLES,
        *WORKSPACE_COLLABORATION_TABLES,
    ):
        _drop_rls(table_name)
    for table_name in reversed(IMMUTABLE_TABLES):
        _drop_append_only(table_name)
    _drop_platform_task_tables()
    _drop_task_reminder_tables()
    _drop_collaboration_tables()
    _drop_commercial_tables()
    _contract_private_storage_idempotency()
    _replace_entitlement_catalog(
        LEGACY_ENTITLEMENT_VALUES,
        previous_length=ENTITLEMENT_LENGTH,
        target_length=LEGACY_ENTITLEMENT_LENGTH,
    )


def _require_no_non_reconstructible_v1_data_before_downgrade() -> None:
    """Refuse the destructive schema contraction once V1 carries real data.

    Code rollback remains supported against the expanded schema.  Contracting
    the schema would permanently discard collaboration history, commercial
    contracts, reminders, internal tasks or canonical-only state, so the
    downgrade is deliberately limited to an unused/empty V1 expansion.

    The owner/organizer mirrors created deterministically by the upgrade are
    the only V1 rows that may be discarded: their authoritative values still
    exist in ``tasks.assigned_to`` and ``meetings.responsible_user_id``.
    """

    bind = op.get_bind()
    blockers: list[str] = []
    for table_name in NON_RECONSTRUCTIBLE_V1_TABLES:
        if bind.execute(
            sa.text(f'SELECT 1 FROM "{table_name}" LIMIT 1')
        ).first() is not None:
            blockers.append(table_name)

    task_state = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM tasks
            WHERE review_required = true
               OR review_due_at IS NOT NULL
               OR review_state <> 'not_required'
               OR review_round <> 0
               OR version <> 1
               OR created_by_user_id IS NOT NULL
               OR cancelled_at IS NOT NULL
               OR cancelled_by_user_id IS NOT NULL
               OR cancellation_reason IS NOT NULL
            LIMIT 1
            """
        )
    ).first()
    if task_state is not None:
        blockers.append("tasks.canonical_v1_state")

    invalid_task_mirror = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM task_participants AS participant
            JOIN tasks AS task ON task.id = participant.task_id
            JOIN matters AS matter ON matter.id = task.matter_id
            JOIN workspace_memberships AS membership
              ON membership.id = participant.membership_id
            WHERE participant.workspace_id <> matter.workspace_id
               OR participant.matter_id <> task.matter_id
               OR participant.user_id <> task.assigned_to
               OR membership.workspace_id <> participant.workspace_id
               OR membership.user_id <> participant.user_id
               OR membership.status <> 'active'
               OR participant.role <> 'owner'
               OR participant.state <> 'assigned'
               OR participant.required_for_completion = true
               OR participant.due_at IS NOT NULL
               OR participant.active = false
               OR participant.created_by_user_id IS NOT NULL
               OR participant.deactivated_at IS NOT NULL
            LIMIT 1
            """
        )
    ).first()
    missing_or_duplicate_task_mirror = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM tasks AS task
            LEFT JOIN task_participants AS participant
              ON participant.task_id = task.id
            GROUP BY task.id
            HAVING COUNT(participant.id) <> 1
            LIMIT 1
            """
        )
    ).first()
    if invalid_task_mirror is not None or missing_or_duplicate_task_mirror is not None:
        blockers.append("task_participants.non_reconstructible")

    meeting_state = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM meetings
            WHERE linked_task_id IS NOT NULL OR version <> 1
            LIMIT 1
            """
        )
    ).first()
    if meeting_state is not None:
        blockers.append("meetings.canonical_v1_state")

    invalid_meeting_mirror = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM meeting_participants AS participant
            JOIN meetings AS meeting ON meeting.id = participant.meeting_id
            JOIN workspace_memberships AS membership
              ON membership.id = participant.membership_id
            WHERE participant.workspace_id <> meeting.workspace_id
               OR participant.user_id <> meeting.responsible_user_id
               OR membership.workspace_id <> participant.workspace_id
               OR membership.user_id <> participant.user_id
               OR membership.status <> 'active'
               OR participant.role <> 'organizer'
               OR participant.rsvp <> 'accepted'
               OR participant.reminder_minutes_before IS NOT NULL
               OR participant.active = false
               OR participant.removed_at IS NOT NULL
            LIMIT 1
            """
        )
    ).first()
    missing_or_duplicate_meeting_mirror = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM meetings AS meeting
            LEFT JOIN meeting_participants AS participant
              ON participant.meeting_id = meeting.id
            WHERE meeting.responsible_user_id IS NOT NULL
            GROUP BY meeting.id
            HAVING COUNT(participant.id) <> 1
            LIMIT 1
            """
        )
    ).first()
    orphan_meeting_mirror = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM meeting_participants AS participant
            JOIN meetings AS meeting ON meeting.id = participant.meeting_id
            WHERE meeting.responsible_user_id IS NULL
            LIMIT 1
            """
        )
    ).first()
    if (
        invalid_meeting_mirror is not None
        or missing_or_duplicate_meeting_mirror is not None
        or orphan_meeting_mirror is not None
    ):
        blockers.append("meeting_participants.non_reconstructible")

    private_storage_state = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM storage_objects
            WHERE idempotency_key_hash IS NOT NULL
            LIMIT 1
            """
        )
    ).first()
    if private_storage_state is not None:
        blockers.append("storage_objects.idempotency_key_hash")

    if blockers:
        raise RuntimeError(
            "V1 schema downgrade is destructive and was refused before any DROP; "
            "retain the expanded schema for code rollback or migrate/export the "
            "following state first: "
            + ", ".join(sorted(set(blockers)))
        )


def _expand_private_storage_idempotency() -> None:
    """Persist only a keyed-upload digest; the raw idempotency key never lands."""

    with op.batch_alter_table("storage_objects") as batch_op:
        batch_op.add_column(
            sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_storage_object_idempotency_key_hash_length",
            "idempotency_key_hash IS NULL OR length(idempotency_key_hash) = 64",
        )
    op.create_index(
        "uq_storage_object_workspace_idempotency_key",
        "storage_objects",
        ["workspace_id", "idempotency_key_hash"],
        unique=True,
    )


def _contract_private_storage_idempotency() -> None:
    op.drop_index(
        "uq_storage_object_workspace_idempotency_key",
        table_name="storage_objects",
    )
    with op.batch_alter_table("storage_objects") as batch_op:
        batch_op.drop_constraint(
            "ck_storage_object_idempotency_key_hash_length",
            type_="check",
        )
        batch_op.drop_column("idempotency_key_hash")


def _create_collaboration_tables() -> None:
    uuid_type = _uuid_type()
    json_type = _json_type()

    # Expand the legacy task in place.  The old columns stay available so the
    # previous application image remains a viable code rollback target.
    op.add_column(
        "tasks",
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("legacy_due_text", sa.String(length=240), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "review_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "review_state",
            sa.String(length=32),
            server_default=sa.text("'not_required'"),
            nullable=False,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "review_round",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("created_by_user_id", uuid_type, nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("cancelled_by_user_id", uuid_type, nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
    )
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.create_unique_constraint(
            "uq_tasks_matter_id_id",
            ["matter_id", "id"],
        )
        batch_op.create_foreign_key(
            "fk_tasks_created_by_user_id",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_tasks_cancelled_by_user_id",
            "users",
            ["cancelled_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "task_review_state_enum",
            "review_state IN ('not_required', 'draft', 'pending', 'approved', "
            "'changes_requested')",
        )
        batch_op.create_check_constraint(
            "ck_tasks_version_positive",
            "version >= 1",
        )
        batch_op.create_check_constraint(
            "ck_tasks_review_round_nonnegative",
            "review_round >= 0",
        )
        batch_op.create_check_constraint(
            "ck_tasks_review_deadline_coherent",
            "review_due_at IS NULL OR "
            "(review_required = true AND due_at IS NOT NULL AND review_due_at <= due_at)",
        )
        batch_op.create_check_constraint(
            "ck_tasks_review_disabled_coherent",
            "review_required = true OR "
            "(review_state = 'not_required' AND review_due_at IS NULL)",
        )
    op.create_index("ix_tasks_due_at", "tasks", ["due_at"])
    op.create_index("ix_tasks_review_due_at", "tasks", ["review_due_at"])
    op.create_index("ix_tasks_review_state", "tasks", ["review_state"])

    op.create_table(
        "task_participants",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("membership_id", uuid_type, nullable=False),
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column(
            "role",
            _enum(
                "task_participant_role_enum",
                "owner",
                "assignee",
                "reviewer",
                "observer",
            ),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "state",
            _enum(
                "task_participant_state_enum",
                "assigned",
                "in_progress",
                "completed",
                "removed",
            ),
            server_default=sa.text("'assigned'"),
            nullable=False,
        ),
        sa.Column(
            "required_for_completion",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("label_snapshot", sa.String(length=240), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "required_for_completion = false OR role = 'assignee'",
            name="ck_task_participants_required_assignee",
        ),
        sa.CheckConstraint(
            "(active = true AND state <> 'removed') OR "
            "(active = false AND state = 'removed')",
            name="ck_task_participants_active_state",
        ),
        sa.ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_participants_matter_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_participants_workspace_matter",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id", "workspace_id", "user_id"],
            [
                "workspace_memberships.id",
                "workspace_memberships.workspace_id",
                "workspace_memberships.user_id",
            ],
            name="fk_task_participants_membership_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "workspace_id",
        "matter_id",
        "task_id",
        "membership_id",
        "user_id",
        "role",
        "state",
        "active",
    ):
        op.create_index(
            f"ix_task_participants_{column}",
            "task_participants",
            [column],
        )
    op.create_index(
        "ix_task_participants_workspace_task",
        "task_participants",
        ["workspace_id", "task_id"],
    )
    op.create_index(
        "ux_task_participants_active_owner",
        "task_participants",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("active = true AND role = 'owner'"),
        sqlite_where=sa.text("active = 1 AND role = 'owner'"),
    )
    op.create_index(
        "ux_task_participants_active_reviewer",
        "task_participants",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("active = true AND role = 'reviewer'"),
        sqlite_where=sa.text("active = 1 AND role = 'reviewer'"),
    )
    op.create_index(
        "ux_task_participants_active_user",
        "task_participants",
        ["task_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("active = true"),
        sqlite_where=sa.text("active = 1"),
    )

    op.create_table(
        "task_review_rounds",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("reviewer_user_id", uuid_type, nullable=False),
        sa.Column(
            "state",
            _enum(
                "task_review_round_state_enum",
                "not_required",
                "draft",
                "pending",
                "approved",
                "changes_requested",
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("submitted_by_user_id", uuid_type, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", uuid_type, nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_review_rounds_matter_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_review_rounds_workspace_matter",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "round_number",
            name="uq_task_review_round_number",
        ),
    )
    for column in ("workspace_id", "matter_id", "task_id"):
        op.create_index(
            f"ix_task_review_rounds_{column}",
            "task_review_rounds",
            [column],
        )
    op.create_index(
        "ix_task_review_rounds_workspace_task",
        "task_review_rounds",
        ["workspace_id", "task_id"],
    )

    op.create_table(
        "task_events",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("actor_user_id", uuid_type, nullable=True),
        sa.Column("actor_label_snapshot", sa.String(length=240), nullable=False),
        sa.Column("actor_role_snapshot", sa.String(length=80), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column(
            "event_type",
            _enum(
                "task_event_type_enum",
                "created",
                "updated",
                "participants_changed",
                "participant_state_changed",
                "started",
                "blocked",
                "unblocked",
                "review_submitted",
                "review_approved",
                "review_changes_requested",
                "completed",
                "reopened",
                "cancelled",
                "comment_added",
            ),
            nullable=False,
        ),
        sa.Column("before_data", json_type, nullable=True),
        sa.Column("after_data", json_type, nullable=True),
        sa.Column("changed_paths", json_type, nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reason_source", sa.String(length=16), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column(
            "session_type",
            sa.String(length=32),
            server_default=sa.text("'normal'"),
            nullable=False,
        ),
        sa.Column("administrative_session_id", uuid_type, nullable=True),
        sa.Column("support_grant_id", uuid_type, nullable=True),
        sa.Column("resource_version_before", sa.Integer(), nullable=True),
        sa.Column("resource_version_after", sa.Integer(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_events_matter_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_events_workspace_matter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "workspace_id",
        "matter_id",
        "task_id",
        "actor_user_id",
        "event_type",
        "occurred_at",
    ):
        op.create_index(
            f"ix_task_events_{column}",
            "task_events",
            [column],
        )
    op.create_index(
        "ix_task_events_workspace_task_occurred",
        "task_events",
        ["workspace_id", "task_id", "occurred_at"],
    )
    op.create_index(
        "ix_task_events_correlation",
        "task_events",
        ["correlation_id"],
    )

    _backfill_legacy_tasks()

    op.add_column(
        "meetings",
        sa.Column("linked_task_id", uuid_type, nullable=True),
    )
    op.add_column(
        "meetings",
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    invalid_meeting_links = list(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT meetings.id FROM meetings "
                "JOIN matters ON matters.id = meetings.matter_id "
                "WHERE meetings.matter_id IS NOT NULL "
                "AND meetings.workspace_id <> matters.workspace_id "
                "ORDER BY meetings.id LIMIT 20"
            )
        )
        .scalars()
    )
    if invalid_meeting_links:
        raise RuntimeError(
            "Meeting collaboration preflight found matter/workspace mismatches: "
            + ", ".join(str(item) for item in invalid_meeting_links)
        )
    with op.batch_alter_table("meetings") as batch_op:
        if _is_postgres():
            batch_op.drop_constraint("meetings_matter_id_fkey", type_="foreignkey")
        batch_op.create_unique_constraint(
            "uq_meetings_workspace_id", ["workspace_id", "id"]
        )
        batch_op.create_unique_constraint("uq_meetings_matter_id", ["matter_id", "id"])
        batch_op.create_foreign_key(
            "fk_meetings_workspace_matter",
            "matters",
            ["workspace_id", "matter_id"],
            ["workspace_id", "id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_meetings_matter_task",
            "tasks",
            ["matter_id", "linked_task_id"],
            ["matter_id", "id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ck_meetings_linked_task_requires_matter",
            "linked_task_id IS NULL OR matter_id IS NOT NULL",
        )
    op.create_index("ix_meetings_linked_task_id", "meetings", ["linked_task_id"])

    op.create_table(
        "meeting_participants",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("membership_id", uuid_type, nullable=False),
        sa.Column("meeting_id", uuid_type, nullable=False),
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column(
            "role",
            _enum(
                "meeting_participant_role_enum",
                "organizer",
                "attendee",
                "observer",
            ),
            nullable=False,
        ),
        sa.Column(
            "rsvp",
            _enum(
                "meeting_participant_rsvp_enum",
                "pending",
                "accepted",
                "declined",
                "tentative",
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("reminder_minutes_before", sa.Integer(), nullable=True),
        sa.Column("label_snapshot", sa.String(length=200), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "meeting_id"],
            ["meetings.workspace_id", "meetings.id"],
            name="fk_meeting_participants_workspace_meeting",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id", "workspace_id", "user_id"],
            [
                "workspace_memberships.id",
                "workspace_memberships.workspace_id",
                "workspace_memberships.user_id",
            ],
            name="fk_meeting_participants_membership_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("workspace_id", "membership_id", "meeting_id", "user_id", "active"):
        op.create_index(
            f"ix_meeting_participants_{column}",
            "meeting_participants",
            [column],
        )
    op.create_index(
        "ix_meeting_participants_workspace_meeting",
        "meeting_participants",
        ["workspace_id", "meeting_id"],
    )
    op.create_index(
        "ux_meeting_participant_active_user",
        "meeting_participants",
        ["meeting_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("active = true"),
        sqlite_where=sa.text("active = 1"),
    )
    op.create_index(
        "ux_meeting_participant_active_organizer",
        "meeting_participants",
        ["meeting_id"],
        unique=True,
        postgresql_where=sa.text("active = true AND role = 'organizer'"),
        sqlite_where=sa.text("active = 1 AND role = 'organizer'"),
    )

    op.create_table(
        "meeting_creation_receipts",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("actor_user_id", uuid_type, nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("meeting_id", uuid_type, nullable=False),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64",
            name="ck_meeting_creation_receipts_payload_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "meeting_id"],
            ["meetings.workspace_id", "meetings.id"],
            name="fk_meeting_creation_receipts_workspace_meeting",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "actor_user_id",
            "idempotency_key",
            name="uq_meeting_creation_receipts_actor_key",
        ),
    )
    for column in ("workspace_id", "actor_user_id", "meeting_id"):
        op.create_index(
            f"ix_meeting_creation_receipts_{column}",
            "meeting_creation_receipts",
            [column],
        )
    op.create_index(
        "ix_meeting_creation_receipts_workspace_created",
        "meeting_creation_receipts",
        ["workspace_id", "created_at"],
    )

    op.create_table(
        "meeting_notification_outbox",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("meeting_id", uuid_type, nullable=True),
        sa.Column("meeting_version", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(length=80), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            _enum(
                "meeting_notification_status_enum",
                "pending",
                "processing",
                "delivered",
                "failed",
                "disabled",
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", uuid_type, nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "meeting_id"],
            ["meetings.workspace_id", "meetings.id"],
            name="fk_meeting_notification_outbox_workspace_meeting",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "dedupe_key",
            name="uq_meeting_notification_outbox_dedupe",
        ),
    )
    for column in ("workspace_id", "meeting_id", "correlation_id"):
        op.create_index(
            f"ix_meeting_notification_outbox_{column}",
            "meeting_notification_outbox",
            [column],
        )
    op.create_index(
        "ix_meeting_notification_outbox_due",
        "meeting_notification_outbox",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_meeting_notification_outbox_workspace_due",
        "meeting_notification_outbox",
        ["workspace_id", "status", "available_at"],
    )

    _backfill_meeting_organizers()


def _parse_legacy_due_date(raw_value: str) -> datetime | None:
    """Parse only ISO values that already contain an explicit time.

    A legacy date-only value carries no authoritative hour or timezone.  It is
    therefore preserved in ``legacy_due_text`` for operator sanitation instead
    of silently turning it into midnight (or any other invented instant).
    """

    normalized = raw_value.strip()
    if not normalized:
        return None
    try:
        if len(normalized) == 10:
            return None
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # A timezone-naive timestamp is not an authoritative instant.  Keep
        # the original value in legacy_due_text for operator sanitation.
        return None
    return parsed.astimezone(UTC)


def _backfill_legacy_tasks() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, due_date, status, updated_at FROM tasks "
            "WHERE due_date IS NOT NULL OR status = 'done'"
        )
    ).mappings()
    for row in rows:
        raw_due = row["due_date"]
        parsed_due = _parse_legacy_due_date(raw_due) if raw_due else None
        bind.execute(
            sa.text(
                "UPDATE tasks SET due_at = :due_at, legacy_due_text = :legacy_due_text, "
                "completed_at = CASE WHEN status = 'done' THEN updated_at ELSE NULL END "
                "WHERE id = :task_id"
            ),
            {
                "due_at": parsed_due,
                "legacy_due_text": None if parsed_due is not None else raw_due,
                "task_id": row["id"],
            },
        )

    candidates = bind.execute(
        sa.text(
            """
            SELECT
                t.id AS task_id,
                t.matter_id,
                m.workspace_id,
                assigned_membership.id AS assigned_membership_id,
                assigned_user.id AS assigned_user_id,
                assigned_user.name AS assigned_user_name,
                t.created_at AS task_created_at
            FROM tasks AS t
            JOIN matters AS m ON m.id = t.matter_id
            LEFT JOIN workspace_memberships AS assigned_membership
                ON assigned_membership.workspace_id = m.workspace_id
               AND assigned_membership.user_id = t.assigned_to
               AND assigned_membership.status = 'active'
            LEFT JOIN users AS assigned_user
                ON assigned_user.id = t.assigned_to
               AND assigned_user.agent_type = 'human'
               AND assigned_user.is_active = true
               AND assigned_membership.user_id = assigned_user.id
            """
        )
    ).mappings()
    candidates = list(candidates)
    unresolved_task_ids = [
        str(row["task_id"]) for row in candidates if row["assigned_user_id"] is None
    ]
    if unresolved_task_ids:
        raise RuntimeError(
            "Task collaboration preflight found tasks without one valid active owner; "
            "reconcile assigned_to and active workspace membership before retrying: "
            + ", ".join(unresolved_task_ids[:20])
        )
    for row in candidates:
        owner_user_id = row["assigned_user_id"]
        owner_label = row["assigned_user_name"]
        participant_id = uuid5(NAMESPACE_URL, f"task-owner:{row['task_id']}")
        created_at = row["task_created_at"]
        bind.execute(
            sa.text(
                """
                INSERT INTO task_participants (
                    id, workspace_id, matter_id, task_id, membership_id,
                    user_id, role, state,
                    required_for_completion, label_snapshot, active,
                    created_by_user_id, created_at, updated_at
                ) VALUES (
                    :id, :workspace_id, :matter_id, :task_id, :membership_id,
                    :user_id, 'owner',
                    'assigned', false, :label_snapshot, true, NULL,
                    :created_at, :created_at
                )
                """
            ),
            {
                "id": _migration_uuid(participant_id),
                "workspace_id": row["workspace_id"],
                "matter_id": row["matter_id"],
                "task_id": row["task_id"],
                "membership_id": row["assigned_membership_id"],
                "user_id": owner_user_id,
                "label_snapshot": owner_label or "Usuário",
                "created_at": created_at,
            },
        )


def _backfill_meeting_organizers() -> None:
    bind = op.get_bind()
    unresolved_meeting_ids = list(
        bind.execute(
            sa.text(
                """
                SELECT m.id
                FROM meetings AS m
                LEFT JOIN users AS u
                  ON u.id = m.responsible_user_id
                 AND u.agent_type = 'human'
                 AND u.is_active = true
                LEFT JOIN workspace_memberships AS wm
                  ON wm.workspace_id = m.workspace_id
                 AND wm.user_id = m.responsible_user_id
                 AND wm.status = 'active'
                WHERE m.responsible_user_id IS NOT NULL
                  AND (u.id IS NULL OR wm.id IS NULL)
                ORDER BY m.id
                LIMIT 20
                """
            )
        ).scalars()
    )
    if unresolved_meeting_ids:
        raise RuntimeError(
            "Meeting collaboration preflight found responsible users without one valid "
            "active human workspace membership; reconcile before retrying: "
            + ", ".join(str(item) for item in unresolved_meeting_ids)
        )
    rows = bind.execute(
        sa.text(
            """
            SELECT m.id AS meeting_id, m.workspace_id, m.responsible_user_id,
                   wm.id AS membership_id, u.name, m.created_at AS meeting_created_at
            FROM meetings AS m
            JOIN users AS u ON u.id = m.responsible_user_id
            JOIN workspace_memberships AS wm
              ON wm.workspace_id = m.workspace_id
             AND wm.user_id = m.responsible_user_id
             AND wm.status = 'active'
            WHERE m.responsible_user_id IS NOT NULL
              AND u.agent_type = 'human'
              AND u.is_active = true
            """
        )
    ).mappings()
    for row in rows:
        participant_id = uuid5(NAMESPACE_URL, f"meeting-organizer:{row['meeting_id']}")
        created_at = row["meeting_created_at"]
        bind.execute(
            sa.text(
                """
                INSERT INTO meeting_participants (
                    id, workspace_id, membership_id, meeting_id, user_id, role, rsvp,
                    label_snapshot, active, created_at, updated_at
                ) VALUES (
                    :id, :workspace_id, :membership_id, :meeting_id,
                    :user_id, 'organizer',
                    'accepted', :label_snapshot, true, :created_at, :created_at
                )
                """
            ),
            {
                "id": _migration_uuid(participant_id),
                "workspace_id": row["workspace_id"],
                "membership_id": row["membership_id"],
                "meeting_id": row["meeting_id"],
                "user_id": row["responsible_user_id"],
                "label_snapshot": row["name"],
                "created_at": created_at,
            },
        )


def _migration_uuid(value: UUID) -> UUID | str:
    return value if _is_postgres() else str(value)


def _create_task_reminder_tables() -> None:
    uuid_type = _uuid_type()
    json_type = _json_type()

    op.create_table(
        "task_reminder_rules",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column(
            "relative_to",
            _enum(
                "task_reminder_relative_to_enum",
                "general_due",
                "personal_due",
                "review_due",
            ),
            nullable=False,
        ),
        sa.Column("offset_minutes_before", sa.Integer(), nullable=False),
        sa.Column(
            "channel",
            _enum("task_reminder_channel_enum", "in_app", "email", "whatsapp"),
            nullable=False,
        ),
        sa.Column(
            "recipient",
            _enum(
                "task_reminder_recipient_enum",
                "owner",
                "assignees",
                "required_assignees",
                "reviewer",
                "specific_user",
            ),
            nullable=False,
        ),
        sa.Column("recipient_user_id", uuid_type, nullable=True),
        sa.Column("recipient_key", sa.String(length=100), nullable=False),
        sa.Column(
            "state",
            _enum(
                "task_reminder_rule_state_enum",
                "scheduled",
                "pending_due",
                "pending_recipient",
                "unavailable",
                "disabled",
            ),
            server_default=sa.text("'pending_due'"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "offset_minutes_before >= 0",
            name="ck_task_reminder_rules_offset_nonnegative",
        ),
        sa.CheckConstraint("version >= 1", name="ck_task_reminder_rules_version_positive"),
        sa.CheckConstraint(
            "(recipient = 'specific_user' AND recipient_user_id IS NOT NULL) OR "
            "(recipient <> 'specific_user' AND recipient_user_id IS NULL)",
            name="ck_task_reminder_rules_specific_recipient",
        ),
        sa.ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_reminder_rules_matter_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_reminder_rules_workspace_matter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "matter_id",
            "task_id",
            "id",
            name="uq_task_reminder_rules_tenant_binding",
        ),
    )
    op.create_index("ix_task_reminder_rules_active", "task_reminder_rules", ["active"])
    op.create_index(
        "ix_task_reminder_rules_workspace_task",
        "task_reminder_rules",
        ["workspace_id", "task_id"],
    )
    op.create_index(
        "ux_task_reminder_rules_active_semantic",
        "task_reminder_rules",
        ["task_id", "relative_to", "offset_minutes_before", "channel", "recipient_key"],
        unique=True,
        postgresql_where=sa.text("active = true"),
        sqlite_where=sa.text("active = 1"),
    )
    for column in ("task_id", "matter_id", "workspace_id"):
        op.create_index(
            f"ix_task_reminder_rules_{column}", "task_reminder_rules", [column]
        )

    op.create_table(
        "task_reminder_outbox",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("rule_id", uuid_type, nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("recipient_user_id", uuid_type, nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "channel",
            _enum("task_reminder_outbox_channel_enum", "in_app", "email", "whatsapp"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "task_reminder_delivery_status_enum",
                "pending",
                "processing",
                "delivered",
                "failed",
                "cancelled",
                "unavailable",
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(length=240), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", uuid_type, nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("notification_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_task_reminder_outbox_attempts_nonnegative"),
        sa.ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_reminder_outbox_matter_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_reminder_outbox_workspace_matter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id", "task_id", "rule_id"],
            [
                "task_reminder_rules.workspace_id",
                "task_reminder_rules.matter_id",
                "task_reminder_rules.task_id",
                "task_reminder_rules.id",
            ],
            name="fk_task_reminder_outbox_tenant_rule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "matter_id",
            "task_id",
            "rule_id",
            "id",
            name="uq_task_reminder_outbox_tenant_binding",
        ),
        sa.UniqueConstraint(
            "workspace_id", "dedupe_key", name="uq_task_reminder_outbox_dedupe"
        ),
    )
    op.create_index(
        "ix_task_reminder_outbox_workspace_due",
        "task_reminder_outbox",
        ["workspace_id", "status", "scheduled_for", "available_at"],
    )
    op.create_index(
        "ix_task_reminder_outbox_due",
        "task_reminder_outbox",
        ["status", "scheduled_for", "available_at"],
    )
    for column in (
        "recipient_user_id",
        "correlation_id",
        "task_id",
        "workspace_id",
        "matter_id",
        "rule_id",
        "status",
        "scheduled_for",
    ):
        op.create_index(
            f"ix_task_reminder_outbox_{column}", "task_reminder_outbox", [column]
        )

    op.create_table(
        "task_reminder_events",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("rule_id", uuid_type, nullable=False),
        sa.Column("outbox_id", uuid_type, nullable=True),
        sa.Column("actor_user_id", uuid_type, nullable=True),
        sa.Column(
            "event_type",
            _enum(
                "task_reminder_event_type_enum",
                "created",
                "updated",
                "deleted",
                "rescheduled",
                "cancelled",
                "delivered",
                "delivery_unavailable",
            ),
            nullable=False,
        ),
        sa.Column("before_data", json_type, nullable=True),
        sa.Column("after_data", json_type, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column("session_type", sa.String(length=32), nullable=False),
        sa.Column("administrative_session_id", uuid_type, nullable=True),
        sa.Column("support_grant_id", uuid_type, nullable=True),
        sa.Column("resource_version_before", sa.Integer(), nullable=True),
        sa.Column("resource_version_after", sa.Integer(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_reminder_events_matter_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_reminder_events_workspace_matter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id", "task_id", "rule_id"],
            [
                "task_reminder_rules.workspace_id",
                "task_reminder_rules.matter_id",
                "task_reminder_rules.task_id",
                "task_reminder_rules.id",
            ],
            name="fk_task_reminder_events_tenant_rule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id", "task_id", "rule_id", "outbox_id"],
            [
                "task_reminder_outbox.workspace_id",
                "task_reminder_outbox.matter_id",
                "task_reminder_outbox.task_id",
                "task_reminder_outbox.rule_id",
                "task_reminder_outbox.id",
            ],
            name="fk_task_reminder_events_tenant_outbox",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_task_reminder_events_rule_id", ["rule_id"]),
        ("ix_task_reminder_events_matter_id", ["matter_id"]),
        ("ix_task_reminder_events_correlation", ["correlation_id"]),
        ("ix_task_reminder_events_workspace_id", ["workspace_id"]),
        (
            "ix_task_reminder_events_workspace_rule_occurred",
            ["workspace_id", "rule_id", "occurred_at"],
        ),
        ("ix_task_reminder_events_occurred_at", ["occurred_at"]),
        ("ix_task_reminder_events_task_id", ["task_id"]),
    ):
        op.create_index(name, "task_reminder_events", columns)

    op.create_table(
        "task_reminder_command_receipts",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("matter_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("rule_id", uuid_type, nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_data", json_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_reminder_receipts_matter_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_reminder_receipts_workspace_matter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id", "task_id", "rule_id"],
            [
                "task_reminder_rules.workspace_id",
                "task_reminder_rules.matter_id",
                "task_reminder_rules.task_id",
                "task_reminder_rules.id",
            ],
            name="fk_task_reminder_receipts_tenant_rule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_task_reminder_command_receipt_key",
        ),
    )
    for name, columns in (
        ("ix_task_reminder_command_receipts_matter_id", ["matter_id"]),
        ("ix_task_reminder_command_receipts_workspace_id", ["workspace_id"]),
        ("ix_task_reminder_command_receipts_task_id", ["task_id"]),
        ("ix_task_reminder_command_receipts_task", ["workspace_id", "task_id"]),
    ):
        op.create_index(name, "task_reminder_command_receipts", columns)


def _drop_task_reminder_tables() -> None:
    op.drop_table("task_reminder_command_receipts")
    op.drop_table("task_reminder_events")
    op.drop_table("task_reminder_outbox")
    op.drop_table("task_reminder_rules")


def _create_platform_task_tables() -> None:
    uuid_type = _uuid_type()
    json_type = _json_type()
    op.create_table(
        "platform_tasks",
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum(
                "platform_task_status_enum",
                "pending",
                "in_progress",
                "blocked",
                "done",
                "cancelled",
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "priority",
            _enum("platform_task_priority_enum", "P0", "P1", "P2", "P3", length=2),
            server_default=sa.text("'P2'"),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "review_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "review_state",
            _enum(
                "platform_task_review_state_enum",
                "not_required",
                "draft",
                "pending",
                "approved",
                "changes_requested",
                "rejected",
            ),
            server_default=sa.text("'not_required'"),
            nullable=False,
        ),
        sa.Column(
            "review_round_number",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("owner_user_id", uuid_type, nullable=False),
        sa.Column("reviewer_user_id", uuid_type, nullable=True),
        sa.Column("created_by_user_id", uuid_type, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("workspace_id", uuid_type, nullable=True),
        sa.Column("linked_ticket_id", uuid_type, nullable=True),
        sa.Column("linked_incident_id", uuid_type, nullable=True),
        sa.Column("linked_lead_id", uuid_type, nullable=True),
        sa.Column("linked_release_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="ck_platform_tasks_version_positive"),
        sa.CheckConstraint(
            "review_round_number >= 0",
            name="ck_platform_tasks_review_round_nonnegative",
        ),
        sa.CheckConstraint(
            "reviewer_user_id IS NULL OR reviewer_user_id <> owner_user_id",
            name="ck_platform_tasks_reviewer_distinct_owner",
        ),
        sa.CheckConstraint(
            "(review_required = true AND reviewer_user_id IS NOT NULL "
            "AND review_state <> 'not_required') OR "
            "(review_required = false AND reviewer_user_id IS NULL "
            "AND review_state = 'not_required')",
            name="ck_platform_tasks_review_contract",
        ),
        sa.CheckConstraint(
            "due_at IS NULL OR owner_due_at IS NULL OR owner_due_at <= due_at",
            name="ck_platform_tasks_owner_deadline_within_task",
        ),
        sa.CheckConstraint(
            "due_at IS NULL OR review_due_at IS NULL OR review_due_at <= due_at",
            name="ck_platform_tasks_review_deadline_within_task",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["linked_ticket_id"],
            ["support_tickets.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["linked_incident_id"],
            ["platform_incidents.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["linked_lead_id"],
            ["growth_prospects.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["linked_release_id"],
            ["platform_releases.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "status",
        "priority",
        "review_state",
        "owner_user_id",
        "reviewer_user_id",
    ):
        op.create_index(
            f"ix_platform_tasks_{column}",
            "platform_tasks",
            [column],
        )
    op.create_index(
        "ix_platform_tasks_status_due",
        "platform_tasks",
        ["status", "due_at"],
    )
    op.create_index(
        "ix_platform_tasks_owner_status_due",
        "platform_tasks",
        ["owner_user_id", "status", "due_at"],
    )
    op.create_index(
        "ix_platform_tasks_reviewer_state_due",
        "platform_tasks",
        ["reviewer_user_id", "review_state", "review_due_at"],
    )
    op.create_index(
        "ix_platform_tasks_workspace_status",
        "platform_tasks",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_platform_tasks_cursor",
        "platform_tasks",
        ["updated_at", "id"],
    )

    op.create_table(
        "platform_task_participants",
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column(
            "role",
            _enum(
                "platform_task_participant_role_enum",
                "assignee",
                "observer",
            ),
            nullable=False,
        ),
        sa.Column("personal_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "state",
            _enum(
                "platform_task_participant_state_enum",
                "assigned",
                "in_progress",
                "completed",
                "removed",
            ),
            server_default=sa.text("'assigned'"),
            nullable=False,
        ),
        sa.Column(
            "required_for_completion",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("label_snapshot", sa.String(length=240), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", uuid_type, nullable=False),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "required_for_completion = false OR role = 'assignee'",
            name="ck_platform_task_participants_required_assignee",
        ),
        sa.CheckConstraint(
            "role <> 'observer' OR personal_due_at IS NULL",
            name="ck_platform_task_participants_observer_without_deadline",
        ),
        sa.CheckConstraint(
            "(active = true AND state <> 'removed' AND removed_at IS NULL) OR "
            "(active = false AND state = 'removed' AND removed_at IS NOT NULL)",
            name="ck_platform_task_participants_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["platform_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_platform_task_participants_active_user",
        "platform_task_participants",
        ["task_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("active = true"),
        sqlite_where=sa.text("active = 1"),
    )
    op.create_index(
        "ix_platform_task_participants_user_state",
        "platform_task_participants",
        ["user_id", "state"],
    )
    op.create_index(
        "ix_platform_task_participants_task_active",
        "platform_task_participants",
        ["task_id", "active"],
    )

    op.create_table(
        "platform_task_review_rounds",
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("reviewer_user_id", uuid_type, nullable=False),
        sa.Column(
            "state",
            _enum(
                "platform_task_review_round_state_enum",
                "pending",
                "approved",
                "changes_requested",
                "rejected",
                "cancelled",
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by_user_id", uuid_type, nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("task_version_reviewed", sa.Integer(), nullable=False),
        sa.Column("resolved_by_user_id", uuid_type, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "round_number > 0",
            name="ck_platform_task_review_round_number_positive",
        ),
        sa.CheckConstraint(
            "task_version_reviewed > 0",
            name="ck_platform_task_review_version_positive",
        ),
        sa.CheckConstraint(
            "(state = 'pending' AND resolved_at IS NULL AND resolved_by_user_id IS NULL) OR "
            "(state <> 'pending' AND resolved_at IS NOT NULL)",
            name="ck_platform_task_review_resolution",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["platform_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "round_number",
            name="uq_platform_task_review_round_number",
        ),
    )
    op.create_index(
        "ix_platform_task_review_round_state_due",
        "platform_task_review_rounds",
        ["state", "review_due_at"],
    )

    op.create_table(
        "platform_task_events",
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("actor_user_id", uuid_type, nullable=True),
        sa.Column("actor_label_snapshot", sa.String(length=240), nullable=False),
        sa.Column("actor_role_snapshot", sa.String(length=80), nullable=False),
        sa.Column("is_platform_root", sa.Boolean(), nullable=False),
        sa.Column(
            "event_type",
            _enum(
                "platform_task_event_type_enum",
                "created",
                "updated",
                "participants_changed",
                "started",
                "blocked",
                "review_submitted",
                "review_approved",
                "review_changes_requested",
                "review_rejected",
                "completed",
                "reopened",
                "cancelled",
                "comment_added",
            ),
            nullable=False,
        ),
        sa.Column("before_data", json_type, nullable=True),
        sa.Column("after_data", json_type, nullable=True),
        sa.Column("changed_paths", json_type, nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column("administrative_session_id", uuid_type, nullable=True),
        sa.Column("session_type", sa.String(length=32), nullable=False),
        sa.Column("resource_version_before", sa.Integer(), nullable=True),
        sa.Column("resource_version_after", sa.Integer(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["platform_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["administrative_session_id"],
            ["administrative_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_task_events_task_occurred",
        "platform_task_events",
        ["task_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_platform_task_events_correlation",
        "platform_task_events",
        ["correlation_id"],
    )


def _drop_platform_task_tables() -> None:
    op.drop_table("platform_task_events")
    op.drop_table("platform_task_review_rounds")
    op.drop_table("platform_task_participants")
    op.drop_table("platform_tasks")


def _drop_collaboration_tables() -> None:
    op.drop_table("meeting_notification_outbox")
    op.drop_table("meeting_creation_receipts")
    op.drop_table("meeting_participants")
    op.drop_index("ix_meetings_linked_task_id", table_name="meetings")
    with op.batch_alter_table("meetings") as batch_op:
        batch_op.drop_constraint(
            "ck_meetings_linked_task_requires_matter", type_="check"
        )
        batch_op.drop_constraint("fk_meetings_matter_task", type_="foreignkey")
        batch_op.drop_constraint("fk_meetings_workspace_matter", type_="foreignkey")
        batch_op.drop_constraint("uq_meetings_matter_id", type_="unique")
        batch_op.drop_constraint("uq_meetings_workspace_id", type_="unique")
        if _is_postgres():
            batch_op.create_foreign_key(
                "meetings_matter_id_fkey",
                "matters",
                ["matter_id"],
                ["id"],
                ondelete="SET NULL",
            )
        batch_op.drop_column("version")
        batch_op.drop_column("linked_task_id")

    op.drop_table("task_events")
    op.drop_table("task_review_rounds")
    op.drop_table("task_participants")
    op.drop_index("ix_tasks_review_state", table_name="tasks")
    op.drop_index("ix_tasks_review_due_at", table_name="tasks")
    op.drop_index("ix_tasks_due_at", table_name="tasks")
    if _is_postgres():
        op.drop_constraint("uq_tasks_matter_id_id", "tasks", type_="unique")
        op.drop_constraint("fk_tasks_created_by_user_id", "tasks", type_="foreignkey")
        op.drop_constraint("fk_tasks_cancelled_by_user_id", "tasks", type_="foreignkey")
        for constraint_name in (
            "task_review_state_enum",
            "ck_tasks_version_positive",
            "ck_tasks_review_round_nonnegative",
            "ck_tasks_review_deadline_coherent",
            "ck_tasks_review_disabled_coherent",
        ):
            op.drop_constraint(constraint_name, "tasks", type_="check")
        for column_name in (
            "cancellation_reason",
            "cancelled_by_user_id",
            "cancelled_at",
            "completed_at",
            "created_by_user_id",
            "version",
            "review_round",
            "review_state",
            "review_required",
            "review_due_at",
            "legacy_due_text",
            "due_at",
        ):
            op.drop_column("tasks", column_name)
    else:
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.drop_constraint("uq_tasks_matter_id_id", type_="unique")
            batch_op.drop_constraint("fk_tasks_created_by_user_id", type_="foreignkey")
            batch_op.drop_constraint("fk_tasks_cancelled_by_user_id", type_="foreignkey")
            for constraint_name in (
                "task_review_state_enum",
                "ck_tasks_version_positive",
                "ck_tasks_review_round_nonnegative",
                "ck_tasks_review_deadline_coherent",
                "ck_tasks_review_disabled_coherent",
            ):
                batch_op.drop_constraint(constraint_name, type_="check")
            for column_name in (
                "cancellation_reason",
                "cancelled_by_user_id",
                "cancelled_at",
                "completed_at",
                "created_by_user_id",
                "version",
                "review_round",
                "review_state",
                "review_required",
                "review_due_at",
                "legacy_due_text",
                "due_at",
            ):
                batch_op.drop_column(column_name)


def _drop_commercial_tables() -> None:
    op.drop_table("commercial_adjustments")
    op.drop_table("workspace_contract_lines")
    op.drop_table("commercial_offer_versions")
    op.drop_table("workspace_commercial_contracts")
    op.drop_table("entitlement_bonuses")
    op.drop_table("commercial_offers")
    op.drop_table("commercial_command_receipts")
    op.drop_table("commercial_catalog_events")
