"""Add native Ads Manager projections and immutable metric evidence.

Revision ID: 2c4e6f8a0d32
Revises: 1b3d5f7a9c21
Create Date: 2026-07-21 06:25:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "2c4e6f8a0d32"
down_revision: str | Sequence[str] | None = "1b3d5f7a9c21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENTITY_TABLE = "klm_ads_manager_entities"
IMPORT_TABLE = "klm_ads_manager_import_receipts"
METRIC_TABLE = "klm_ads_metric_observations"
TABLES = (ENTITY_TABLE, IMPORT_TABLE, METRIC_TABLE)
IMMUTABLE_TABLES = (IMPORT_TABLE, METRIC_TABLE)
WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in reversed(TABLES):
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def _enable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION klm_reject_append_only_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table_name in IMMUTABLE_TABLES:
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable"
                BEFORE UPDATE OR DELETE ON "{table_name}"
                FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
    elif dialect == "sqlite":
        for table_name in IMMUTABLE_TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    f'''
                    CREATE TRIGGER IF NOT EXISTS "trg_{table_name}_immutable_{operation}"
                    BEFORE {operation.upper()} ON "{table_name}"
                    BEGIN
                        SELECT RAISE(ABORT, '{table_name} is immutable');
                    END
                    '''
                )


def _disable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table_name in reversed(IMMUTABLE_TABLES):
            op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable" ON "{table_name}"')
    elif dialect == "sqlite":
        for table_name in reversed(IMMUTABLE_TABLES):
            for operation in ("update", "delete"):
                op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_{operation}"')


def upgrade() -> None:
    op.create_table(
        ENTITY_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("parent_external_id", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "effective_status",
            sa.String(length=40),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("configured_status", sa.String(length=40), nullable=True),
        sa.Column("objective", sa.String(length=120), nullable=True),
        sa.Column("buying_type", sa.String(length=80), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("timezone", sa.String(length=80), nullable=True),
        sa.Column("hierarchy_path", FlexibleJSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "entity_metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
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
            "entity_type IN ('account','campaign','ad_set','ad','creative','form')",
            name="ck_klm_ads_entity_type",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_ads_entity_workspace_connection",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            "entity_type",
            "external_id",
            name="uq_klm_ads_entity_external",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_ads_entity_workspace_id"),
    )
    for column in (
        "workspace_id",
        "connection_id",
        "provider",
        "entity_type",
        "external_id",
        "parent_external_id",
        "effective_status",
        "last_seen_at",
    ):
        op.create_index(f"ix_{ENTITY_TABLE}_{column}", ENTITY_TABLE, [column])
    op.create_index(
        "ix_klm_ads_entity_workspace_provider_type_status",
        ENTITY_TABLE,
        ["workspace_id", "provider", "entity_type", "effective_status"],
    )

    op.create_table(
        IMPORT_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_sync_id", sa.String(length=255), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("entity_count", sa.Integer(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "import_metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_ads_import_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            "external_sync_id",
            name="uq_klm_ads_import_external_sync",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_ads_import_workspace_id"),
    )
    for column in ("workspace_id", "connection_id", "provider", "source", "received_at"):
        op.create_index(f"ix_{IMPORT_TABLE}_{column}", IMPORT_TABLE, [column])

    op.create_table(
        METRIC_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("entity_id", FlexibleUUID(), nullable=False),
        sa.Column("import_receipt_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_sync_id", sa.String(length=255), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("attribution_window", sa.String(length=80), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="BRL", nullable=False),
        sa.Column("spend", sa.Numeric(18, 6), server_default="0", nullable=False),
        sa.Column("impressions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reach", sa.Integer(), server_default="0", nullable=False),
        sa.Column("clicks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("conversations", sa.Integer(), server_default="0", nullable=False),
        sa.Column("comments", sa.Integer(), server_default="0", nullable=False),
        sa.Column("leads", sa.Integer(), server_default="0", nullable=False),
        sa.Column("qualified_leads", sa.Integer(), server_default="0", nullable=False),
        sa.Column("opportunities", sa.Integer(), server_default="0", nullable=False),
        sa.Column("contracts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revenue", sa.Numeric(18, 6), server_default="0", nullable=False),
        sa.Column(
            "provider_metrics",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint("spend >= 0", name="ck_klm_ads_metric_spend_nonnegative"),
        sa.CheckConstraint("revenue >= 0", name="ck_klm_ads_metric_revenue_nonnegative"),
        sa.CheckConstraint(
            "impressions >= 0 AND reach >= 0 AND clicks >= 0 AND leads >= 0",
            name="ck_klm_ads_metric_counts_nonnegative",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "entity_id"],
            ["klm_ads_manager_entities.workspace_id", "klm_ads_manager_entities.id"],
            name="fk_klm_ads_metric_workspace_entity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "import_receipt_id"],
            [
                "klm_ads_manager_import_receipts.workspace_id",
                "klm_ads_manager_import_receipts.id",
            ],
            name="fk_klm_ads_metric_workspace_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "entity_id",
            "metric_date",
            "external_sync_id",
            name="uq_klm_ads_metric_observation",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_ads_metric_workspace_id"),
    )
    for column in (
        "workspace_id",
        "entity_id",
        "import_receipt_id",
        "provider",
        "metric_date",
        "observed_at",
    ):
        op.create_index(f"ix_{METRIC_TABLE}_{column}", METRIC_TABLE, [column])
    op.create_index(
        "ix_klm_ads_metric_workspace_provider_date",
        METRIC_TABLE,
        ["workspace_id", "provider", "metric_date"],
    )
    _enable_rls()
    _enable_append_only_guards()


def downgrade() -> None:
    _disable_append_only_guards()
    _disable_rls()
    op.drop_index("ix_klm_ads_metric_workspace_provider_date", table_name=METRIC_TABLE)
    for column in reversed(
        ("workspace_id", "entity_id", "import_receipt_id", "provider", "metric_date", "observed_at")
    ):
        op.drop_index(f"ix_{METRIC_TABLE}_{column}", table_name=METRIC_TABLE)
    op.drop_table(METRIC_TABLE)
    for column in reversed(("workspace_id", "connection_id", "provider", "source", "received_at")):
        op.drop_index(f"ix_{IMPORT_TABLE}_{column}", table_name=IMPORT_TABLE)
    op.drop_table(IMPORT_TABLE)
    op.drop_index("ix_klm_ads_entity_workspace_provider_type_status", table_name=ENTITY_TABLE)
    for column in reversed(
        (
            "workspace_id",
            "connection_id",
            "provider",
            "entity_type",
            "external_id",
            "parent_external_id",
            "effective_status",
            "last_seen_at",
        )
    ):
        op.drop_index(f"ix_{ENTITY_TABLE}_{column}", table_name=ENTITY_TABLE)
    op.drop_table(ENTITY_TABLE)
