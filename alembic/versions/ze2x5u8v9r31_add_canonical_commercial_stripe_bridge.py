"""Add canonical commercial Stripe bridge.

Revision ID: ze2x5u8v9r31
Revises: zd1w4t7u8q20
"""

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision = "ze2x5u8v9r31"
down_revision = "zd1w4t7u8q20"
branch_labels = None
depends_on = None

PLATFORM_ACTOR_SETTING = "app.current_platform_actor_id"
PLATFORM_SCOPE_SETTING = "app.current_platform_scope"
PLATFORM_ACTION_SETTING = "app.current_platform_action"
PLATFORM_READ_POLICY = "platform_commercial_read"
PLATFORM_INSERT_POLICY = "platform_commercial_insert"
PLATFORM_UPDATE_POLICY = "platform_commercial_update"
PROVIDER_RECONCILE_CAPABILITY = ("billing_provider", "reconcile")

COMMERCIAL_TABLES = (
    "commercial_offers",
    "commercial_offer_versions",
    "workspace_commercial_contracts",
    "workspace_contract_lines",
    "commercial_adjustments",
    "entitlement_bonuses",
    "commercial_command_receipts",
    "commercial_catalog_events",
)

WORKFLOW_CAPABILITIES = (
    ("sales", "manage"),
    ("customers", "manage"),
    ("finance", "manage"),
)
PORTABILITY_REBIND = ("commercial_portability", "rebind")
PORTABILITY_RESTORE_REBIND = ("commercial_portability", "restore_rebind")

PREVIOUS_READ_CAPABILITIES = {
    "commercial_offers": (
        ("plans", "view"),
        ("plans", "manage"),
        ("plans", "draft"),
        ("plans", "preview"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        *WORKFLOW_CAPABILITIES,
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
        *WORKFLOW_CAPABILITIES,
    ),
    "workspace_commercial_contracts": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "view"),
        ("commercial_adjustments", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "workspace_contract_lines": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "commercial_adjustments": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "view"),
        ("commercial_adjustments", "manage"),
        *WORKFLOW_CAPABILITIES,
        PORTABILITY_REBIND,
        PORTABILITY_RESTORE_REBIND,
    ),
    "entitlement_bonuses": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("entitlement_bonuses", "view"),
        ("entitlement_bonuses", "manage"),
        *WORKFLOW_CAPABILITIES,
        PORTABILITY_REBIND,
    ),
    "commercial_command_receipts": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
        *WORKFLOW_CAPABILITIES,
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
        *WORKFLOW_CAPABILITIES,
    ),
}

PREVIOUS_INSERT_CAPABILITIES = {
    "commercial_offers": (),
    "commercial_offer_versions": (),
    "workspace_commercial_contracts": (
        ("commercial_contracts", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "workspace_contract_lines": (
        ("commercial_contracts", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "commercial_adjustments": (("commercial_adjustments", "manage"),),
    "entitlement_bonuses": (("entitlement_bonuses", "manage"),),
    "commercial_command_receipts": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "commercial_catalog_events": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
}

PREVIOUS_UPDATE_CAPABILITIES = {
    "workspace_commercial_contracts": (
        ("commercial_contracts", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "workspace_contract_lines": (
        ("commercial_contracts", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "commercial_adjustments": (
        ("commercial_adjustments", "manage"),
        PORTABILITY_REBIND,
        PORTABILITY_RESTORE_REBIND,
    ),
    "entitlement_bonuses": (
        ("entitlement_bonuses", "manage"),
        PORTABILITY_REBIND,
    ),
}

PROVIDER_INSERT_TABLES = {
    "workspace_commercial_contracts",
    "workspace_contract_lines",
    "commercial_command_receipts",
    "commercial_catalog_events",
}


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


def _alter_policy(
    table_name: str,
    policy_name: str,
    *,
    using: str | None = None,
    with_check: str | None = None,
) -> None:
    clauses = [f'ALTER POLICY "{policy_name}" ON "{table_name}"']
    if using is not None:
        clauses.append(f"USING ({using})")
    if with_check is not None:
        clauses.append(f"WITH CHECK ({with_check})")
    op.execute("\n".join(clauses))


def _install_provider_reconcile_policies(*, enabled: bool) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in COMMERCIAL_TABLES:
        read_capabilities = PREVIOUS_READ_CAPABILITIES[table_name]
        if enabled:
            read_capabilities = (*read_capabilities, PROVIDER_RECONCILE_CAPABILITY)
        _alter_policy(
            table_name,
            PLATFORM_READ_POLICY,
            using=_platform_predicate(*read_capabilities),
        )

        insert_capabilities = PREVIOUS_INSERT_CAPABILITIES[table_name]
        if enabled and table_name in PROVIDER_INSERT_TABLES:
            insert_capabilities = (*insert_capabilities, PROVIDER_RECONCILE_CAPABILITY)
        _alter_policy(
            table_name,
            PLATFORM_INSERT_POLICY,
            with_check=_platform_predicate(*insert_capabilities),
        )

        if table_name in PREVIOUS_UPDATE_CAPABILITIES:
            update_capabilities = PREVIOUS_UPDATE_CAPABILITIES[table_name]
            if enabled:
                update_capabilities = (*update_capabilities, PROVIDER_RECONCILE_CAPABILITY)
            predicate = _platform_predicate(*update_capabilities)
            _alter_policy(
                table_name,
                PLATFORM_UPDATE_POLICY,
                using=predicate,
                with_check=predicate,
            )


def upgrade() -> None:
    uuid_type = FlexibleUUID()
    op.create_table(
        "commercial_provider_price_bindings",
        sa.Column("offer_id", uuid_type, nullable=False),
        sa.Column("offer_version_id", uuid_type, nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("billing_interval", sa.String(20), nullable=False),
        sa.Column("provider_price_id", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("configured_by_user_id", uuid_type),
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["offer_version_id", "offer_id"],
            ["commercial_offer_versions.id", "commercial_offer_versions.offer_id"],
            ondelete="RESTRICT",
            name="fk_commercial_provider_price_offer_version",
        ),
        sa.ForeignKeyConstraint(["configured_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("version > 0", name="ck_commercial_provider_price_version"),
        sa.CheckConstraint(
            "length(trim(provider_price_id)) >= 3", name="ck_commercial_provider_price_id"
        ),
        sa.CheckConstraint("length(trim(reason)) >= 8", name="ck_commercial_provider_price_reason"),
    )
    op.create_index(
        "uq_commercial_provider_price_active",
        "commercial_provider_price_bindings",
        ["provider", "offer_version_id", "billing_interval"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_commercial_provider_price_id_active",
        "commercial_provider_price_bindings",
        ["provider", "provider_price_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "commercial_contract_provider_bindings",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("contract_id", uuid_type, nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_customer_id", sa.String(200)),
        sa.Column("provider_subscription_id", sa.String(200)),
        sa.Column("provider_status", sa.String(40)),
        sa.Column("last_event_id", sa.String(200)),
        sa.Column("last_event_created", sa.BigInteger()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["workspace_commercial_contracts.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("contract_id", "provider", name="uq_commercial_contract_provider"),
        sa.CheckConstraint("version > 0", name="ck_commercial_contract_provider_version"),
    )
    op.create_index(
        "uq_commercial_provider_customer",
        "commercial_contract_provider_bindings",
        ["provider", "provider_customer_id"],
        unique=True,
        postgresql_where=sa.text("provider_customer_id IS NOT NULL"),
    )
    op.create_index(
        "uq_commercial_provider_subscription",
        "commercial_contract_provider_bindings",
        ["provider", "provider_subscription_id"],
        unique=True,
        postgresql_where=sa.text("provider_subscription_id IS NOT NULL"),
    )

    op.create_table(
        "commercial_checkout_intents",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("contract_id", uuid_type, nullable=False),
        sa.Column("actor_user_id", uuid_type),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("target_sku", sa.String(80), nullable=False),
        sa.Column("target_offer_version_id", uuid_type, nullable=False),
        sa.Column("expected_contract_version", sa.Integer(), nullable=False),
        sa.Column("target_lines", FlexibleJSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("provider_session_id", sa.String(200)),
        sa.Column("checkout_url", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="prepared"),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["workspace_commercial_contracts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["target_offer_version_id"], ["commercial_offer_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_commercial_checkout_idempotency"
        ),
        sa.UniqueConstraint(
            "provider", "provider_session_id", name="uq_commercial_checkout_provider_session"
        ),
        sa.CheckConstraint("expected_contract_version > 0", name="ck_checkout_contract_version"),
        sa.CheckConstraint("version > 0", name="ck_checkout_intent_version"),
        sa.CheckConstraint("length(trim(reason)) >= 8", name="ck_checkout_intent_reason"),
    )
    op.create_index(
        "ix_commercial_checkout_workspace_status",
        "commercial_checkout_intents",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_commercial_checkout_contract",
        "commercial_checkout_intents",
        ["contract_id", "created_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = NULLIF(current_setting('app.current_workspace_id', true), '')"
        )
        op.execute('ALTER TABLE "commercial_checkout_intents" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "commercial_checkout_intents" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""CREATE POLICY "workspace_current_id" ON "commercial_checkout_intents"
            AS PERMISSIVE FOR ALL TO PUBLIC USING ({predicate}) WITH CHECK ({predicate})"""
        )
    _install_provider_reconcile_policies(enabled=True)


def downgrade() -> None:
    _install_provider_reconcile_policies(enabled=False)
    op.drop_table("commercial_checkout_intents")
    op.drop_table("commercial_contract_provider_bindings")
    op.drop_table("commercial_provider_price_bindings")
