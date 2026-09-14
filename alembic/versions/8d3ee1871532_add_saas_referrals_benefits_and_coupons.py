"""Adiciona indicacoes SaaS, beneficios, Partner e Studio de Cupons.

Revision ID: 8d3ee1871532
Revises: 7c2cc0760421
Create Date: 2026-07-26 02:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID


revision: str = "8d3ee1871532"
down_revision: str | Sequence[str] | None = "7c2cc0760421"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
PLATFORM_SCOPE_SETTING = "app.current_platform_scope"
PLATFORM_SCOPE = "saas_benefits"
RLS_POLICY = "workspace_or_benefits_blackbox"
STANDARD_TENANT_TABLES = (
    "saas_referral_profiles",
    "benefit_grants",
    "benefit_ledger_entries",
    "partner_memberships",
    "coupon_redemptions",
)
IMMUTABLE_TABLES = (
    "referral_program_policies",
    "benefit_ledger_entries",
    "coupon_audit_events",
)


def _enum(name: str, *values: str, length: int = 32) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def _workspace_predicate(column: str = "workspace_id") -> str:
    return (
        f"({column}::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '') "
        f"OR current_setting('{PLATFORM_SCOPE_SETTING}', true) = '{PLATFORM_SCOPE}')"
    )


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in STANDARD_TENANT_TABLES:
        predicate = _workspace_predicate()
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )
    attribution_predicate = (
        f"(referrer_workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '') "
        f"OR referred_workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '') "
        f"OR current_setting('{PLATFORM_SCOPE_SETTING}', true) = '{PLATFORM_SCOPE}')"
    )
    op.execute('ALTER TABLE "saas_referral_attributions" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "saas_referral_attributions" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "saas_referral_attributions"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({attribution_predicate}) WITH CHECK ({attribution_predicate})'''
    )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in (*STANDARD_TENANT_TABLES, "saas_referral_attributions"):
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def _enable_append_only_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
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


def _disable_append_only_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in IMMUTABLE_TABLES:
        op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable" ON "{table_name}"')


def upgrade() -> None:
    op.create_table(
        "referral_program_policies",
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            _enum("referral_policy_status_enum", "draft", "published", "archived"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("rules_json", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", name="uq_referral_program_policies_version"),
    )
    op.create_index(
        "ix_referral_program_policies_status_effective",
        "referral_program_policies",
        ["status", "effective_at"],
    )

    op.create_table(
        "saas_referral_profiles",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_saas_referral_profiles_workspace"),
        sa.UniqueConstraint("code", name="uq_saas_referral_profiles_code"),
    )
    op.create_index("ix_saas_referral_profiles_active", "saas_referral_profiles", ["active"])

    op.create_table(
        "saas_referral_attributions",
        sa.Column("referral_profile_id", FlexibleUUID(), nullable=False),
        sa.Column("referrer_workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("referred_workspace_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "saas_referral_status_enum",
                "registered",
                "qualified",
                "payment_hold",
                "eligible",
                "rewarded",
                "reversed",
            ),
            nullable=False,
            server_default="registered",
        ),
        sa.Column("plan_code", sa.String(length=32), nullable=True),
        sa.Column("first_payment_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hold_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rewarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="referral_link"),
        sa.Column("metadata_json", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "referrer_workspace_id <> referred_workspace_id",
            name="ck_saas_referral_attributions_distinct_workspaces",
        ),
        sa.ForeignKeyConstraint(
            ["referral_profile_id"], ["saas_referral_profiles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["referrer_workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["referred_workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "referred_workspace_id", name="uq_saas_referral_attributions_referred"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_saas_referral_attributions_idempotency"
        ),
    )
    op.create_index(
        "ix_saas_referral_attributions_referrer_status",
        "saas_referral_attributions",
        ["referrer_workspace_id", "status"],
    )
    op.create_index(
        "ix_saas_referral_attributions_hold",
        "saas_referral_attributions",
        ["status", "hold_until"],
    )

    op.create_table(
        "benefit_grants",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("benefit_type", sa.String(length=48), nullable=False),
        sa.Column(
            "unit",
            _enum("benefit_unit_enum", "days", "months", "tokens", "percentage", "cents"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "status",
            _enum("benefit_status_enum", "pending", "available", "applied", "reversed", "expired"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_ref", sa.String(length=160), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("ticket_id", FlexibleUUID(), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("approved_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("amount > 0", name="ck_benefit_grants_amount_positive"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_benefit_grants_idempotency"),
    )
    op.create_index(
        "ix_benefit_grants_workspace_status", "benefit_grants", ["workspace_id", "status"]
    )
    op.create_index(
        "ix_benefit_grants_source", "benefit_grants", ["source_type", "source_ref"]
    )

    op.create_table(
        "benefit_ledger_entries",
        sa.Column("grant_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column(
            "unit",
            _enum("benefit_ledger_unit_enum", "days", "months", "tokens", "percentage", "cents"),
            nullable=False,
        ),
        sa.Column("delta", sa.Numeric(18, 2), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("ticket_id", FlexibleUUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metadata_json", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["grant_id"], ["benefit_grants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grant_id", "event_type", name="uq_benefit_ledger_grant_event"),
    )
    op.create_index(
        "ix_benefit_ledger_entries_workspace_created",
        "benefit_ledger_entries",
        ["workspace_id", "created_at"],
    )
    op.create_index("ix_benefit_ledger_entries_grant", "benefit_ledger_entries", ["grant_id"])

    op.create_table(
        "partner_memberships",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "status",
            _enum("partner_status_enum", "candidate", "active", "suspended", "expired"),
            nullable=False,
            server_default="candidate",
        ),
        sa.Column("tier", sa.String(length=40), nullable=False, server_default="partner"),
        sa.Column("active_referrals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("referred_mrr_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exemption_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("metadata_json", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_partner_memberships_workspace"),
    )
    op.create_index(
        "ix_partner_memberships_status_review",
        "partner_memberships",
        ["status", "next_review_at"],
    )

    op.create_table(
        "coupons",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status",
            _enum("coupon_status_enum", "draft", "active", "paused", "expired", "archived"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("benefit_type", sa.String(length=48), nullable=False),
        sa.Column(
            "unit",
            _enum("coupon_benefit_unit_enum", "days", "months", "tokens", "percentage", "cents"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("allowed_plan_codes", FlexibleJSON(), nullable=False, server_default="[]"),
        sa.Column("total_limit", sa.Integer(), nullable=True),
        sa.Column("per_workspace_limit", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("redeemed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auto_apply", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("updated_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("metadata_json", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("amount > 0", name="ck_coupons_amount_positive"),
        sa.CheckConstraint("total_limit IS NULL OR total_limit > 0", name="ck_coupons_total_limit"),
        sa.CheckConstraint("per_workspace_limit > 0", name="ck_coupons_workspace_limit"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_coupons_code"),
    )
    op.create_index(
        "ix_coupons_status_window", "coupons", ["status", "starts_at", "ends_at"]
    )

    op.create_table(
        "coupon_redemptions",
        sa.Column("coupon_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("benefit_grant_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "status",
            _enum("coupon_redemption_status_enum", "available", "applied", "reversed"),
            nullable=False,
            server_default="applied",
        ),
        sa.Column("redeemed_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupons.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["benefit_grant_id"], ["benefit_grants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["redeemed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_coupon_redemptions_idempotency"),
    )
    op.create_index(
        "ix_coupon_redemptions_coupon_workspace",
        "coupon_redemptions",
        ["coupon_id", "workspace_id"],
    )
    op.create_index(
        "ix_coupon_redemptions_workspace",
        "coupon_redemptions",
        ["workspace_id", "redeemed_at"],
    )

    op.create_table(
        "coupon_audit_events",
        sa.Column("coupon_id", FlexibleUUID(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("ticket_id", FlexibleUUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("before_json", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("after_json", FlexibleJSON(), nullable=False, server_default="{}"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupons.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_coupon_audit_events_coupon_created",
        "coupon_audit_events",
        ["coupon_id", "created_at"],
    )

    _enable_rls()
    _enable_append_only_guards()


def downgrade() -> None:
    _disable_append_only_guards()
    _disable_rls()
    op.drop_table("coupon_audit_events")
    op.drop_table("coupon_redemptions")
    op.drop_table("coupons")
    op.drop_table("partner_memberships")
    op.drop_table("benefit_ledger_entries")
    op.drop_table("benefit_grants")
    op.drop_table("saas_referral_attributions")
    op.drop_table("saas_referral_profiles")
    op.drop_table("referral_program_policies")
