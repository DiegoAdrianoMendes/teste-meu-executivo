"""add CMED medication catalog and tenant treatment estimates

Revision ID: 94e5f6a7b8c9
Revises: 93d4e5f6a7b8
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "94e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "93d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"


def upgrade() -> None:
    op.create_table(
        "medication_references",
        sa.Column("catalog_key", sa.String(length=64), nullable=False),
        sa.Column("dataset_version", sa.String(length=96), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=300), nullable=False),
        sa.Column("active_ingredient", sa.String(length=500), nullable=True),
        sa.Column("laboratory", sa.String(length=500), nullable=True),
        sa.Column("probable_area", sa.String(length=220), nullable=True),
        sa.Column("oncology", sa.Boolean(), nullable=False),
        sa.Column("injunction_type", sa.String(length=300), nullable=True),
        sa.Column("therapeutic_class_cmed", sa.Text(), nullable=True),
        sa.Column("cmed_presentation", sa.Text(), nullable=False),
        sa.Column("anvisa_registration", sa.String(length=80), nullable=True),
        sa.Column("ggrem_code", sa.String(length=80), nullable=True),
        sa.Column("pmvg_without_tax", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("pmvg_18", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("pf_without_tax", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("cap_applies", sa.Boolean(), nullable=False),
        sa.Column("hospital_restriction", sa.Boolean(), nullable=False),
        sa.Column("commercialized_2025", sa.Boolean(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("practical_legal_observation", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_dataset_label", sa.String(length=220), nullable=False),
        sa.Column("source_rows", FlexibleJSON(), nullable=False),
        sa.Column("source_ranks", FlexibleJSON(), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("catalog_key", name="uq_medication_reference_catalog_key"),
    )
    op.create_index(
        "ix_medication_reference_dataset_product",
        "medication_references",
        ["dataset_version", "product_name"],
    )
    op.create_index(
        "ix_medication_reference_active_ingredient",
        "medication_references",
        ["active_ingredient"],
    )
    op.create_index(
        "ix_medication_reference_ggrem", "medication_references", ["ggrem_code"]
    )
    op.create_index(
        "ix_medication_references_oncology", "medication_references", ["oncology"]
    )

    op.create_table(
        "matter_treatment_estimates",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=False),
        sa.Column("medication_reference_id", FlexibleUUID(), nullable=False),
        sa.Column("presentations_per_cycle", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("cycle_count", sa.Integer(), nullable=False),
        sa.Column("cycle_interval_days", sa.Integer(), nullable=True),
        sa.Column("treatment_duration_days", sa.Integer(), nullable=True),
        sa.Column("cost_per_presentation", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("total_treatment_cost", sa.Numeric(precision=22, scale=2), nullable=False),
        sa.Column("calculation_formula", sa.Text(), nullable=False),
        sa.Column("prescription_reference", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(length=24), nullable=False),
        sa.Column("applied_to_matter_economic_value", sa.Boolean(), nullable=False),
        sa.Column("source_snapshot", FlexibleJSON(), nullable=False),
        sa.Column("reviewed_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["medication_reference_id"], ["medication_references.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "matter_id",
            "medication_reference_id",
            name="uq_matter_treatment_estimate_current",
        ),
    )
    op.create_index(
        "ix_treatment_estimate_workspace_matter",
        "matter_treatment_estimates",
        ["workspace_id", "matter_id"],
    )
    op.create_index(
        "ix_matter_treatment_estimates_review_status",
        "matter_treatment_estimates",
        ["review_status"],
    )

    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = "
            f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
        )
        op.execute('ALTER TABLE "matter_treatment_estimates" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "matter_treatment_estimates" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""
            CREATE POLICY "{POLICY_NAME}" ON "matter_treatment_estimates"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "matter_treatment_estimates"'
        )
    op.drop_index(
        "ix_matter_treatment_estimates_review_status",
        table_name="matter_treatment_estimates",
    )
    op.drop_index(
        "ix_treatment_estimate_workspace_matter",
        table_name="matter_treatment_estimates",
    )
    op.drop_table("matter_treatment_estimates")
    op.drop_index("ix_medication_references_oncology", table_name="medication_references")
    op.drop_index("ix_medication_reference_ggrem", table_name="medication_references")
    op.drop_index(
        "ix_medication_reference_active_ingredient", table_name="medication_references"
    )
    op.drop_index(
        "ix_medication_reference_dataset_product", table_name="medication_references"
    )
    op.drop_table("medication_references")
