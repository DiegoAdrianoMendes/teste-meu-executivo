from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "8d3ee1871532_add_saas_referrals_benefits_and_coupons.py"
)


def test_migracao_define_isolamento_e_imutabilidade_dos_beneficios_saas() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    for table_name in (
        "saas_referral_profiles",
        "benefit_grants",
        "benefit_ledger_entries",
        "partner_memberships",
        "coupon_redemptions",
    ):
        assert f'"{table_name}"' in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "app.current_workspace_id" in source
    assert "app.current_platform_scope" in source
    assert "saas_benefits" in source
    assert "referrer_workspace_id::text" in source
    assert "referred_workspace_id::text" in source

    for table_name in (
        "referral_program_policies",
        "benefit_ledger_entries",
        "coupon_audit_events",
    ):
        assert f'"{table_name}"' in source
    assert "BEFORE UPDATE OR DELETE" in source
    assert "klm_reject_append_only_mutation" in source
