from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/RUNBOOK_EXPAND_CONTRACT_RDS_RECOVERY.md"
AWS_RUNBOOK = ROOT / "docs/RUNBOOK_AWS_FASE1.md"
RLS_RUNBOOK = ROOT / "docs/POSTGRESQL_RLS.md"
WORKFLOW = ROOT / ".github/workflows/deploy.yml"
MIGRATION_MODULE = ROOT / "app/db/aws_database_roles.py"
EXPECTED_HEAD = "e8c0a2d4f617"


def test_greenfield_runbook_denies_legacy_binary_and_requires_snapshot_restore() -> None:
    content = RUNBOOK.read_text(encoding="utf-8")

    for marker in (
        "a671ed1",
        "não é rollback-compatible",
        "NOLOGIN NOBYPASSRLS",
        "runtime_state=PREPARED",
        "ecs_services_mutated=false",
        "frontends_published=false",
        "dns_cutover=false",
        "restaure o snapshot para uma nova instância",
        "não possui `cloudformation:*`",
        "não provam estado AWS de produção",
    ):
        assert marker in content
    assert f"migration bloqueante até o head `{EXPECTED_HEAD}`" in content


def test_all_operational_runbooks_name_the_current_greenfield_head() -> None:
    aws_content = AWS_RUNBOOK.read_text(encoding="utf-8")
    rls_content = RLS_RUNBOOK.read_text(encoding="utf-8")

    assert f"head seguro `{EXPECTED_HEAD}`" in aws_content
    assert f"The greenfield head `{EXPECTED_HEAD}`" in rls_content


def test_rls_runbook_uses_the_canonical_inventory_instead_of_a_stale_count() -> None:
    content = RLS_RUNBOOK.read_text(encoding="utf-8")

    assert "`RLS_TENANT_TABLES`" in content
    assert "`RLS_POLICY_SPECS`" in content
    assert "`set(RLS_TENANT_TABLES) == set(RLS_POLICY_SPECS)`" in content
    assert not re.search(
        r"\b(?:protects?|covers?|isolates?|inventory(?:\s+of)?|all)\s+"
        r"(?:these\s+)?\d+\s+(?:tenant[- ]aware\s+)?tables?\b",
        content,
        flags=re.IGNORECASE,
    )


def test_greenfield_workflow_stops_before_runtime_frontend_and_dns() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    migration = workflow.index("Run blocking ECS migration")
    evidence = workflow.index("Build final migration evidence")
    stop = workflow.index("Record final artifact digest and stop before ENABLED")
    assert migration < evidence < stop
    assert "./scripts/deploy_aws_ecs_v1.sh" not in workflow
    assert "./scripts/publish_aws_frontends_v1.sh" not in workflow
    assert "KLM_PREVIOUS_APP_REF" not in workflow
    assert 'runtimeStateObserved: "PREPARED"' in workflow
    assert "runtimeEnabled: false" in workflow


def test_migration_contracts_grants_and_catalog_after_head_without_master() -> None:
    content = MIGRATION_MODULE.read_text(encoding="utf-8")
    upgrade = content.index('["alembic", "upgrade", "head"]')
    reconcile_call = content.index(
        "reconcile_runtime_privileges_as_migrator(database_url, expected_role)",
        upgrade,
    )

    assert upgrade < reconcile_call
    assert "_discover_runtime_role_from_resolver_acl" in content
    assert "_verify_resolver_owner_contract" in content
    assert "_verify_exact_runtime_dml" in content
    assert "Resolver-owner SELECT policies or FORCE RLS state drifted" in content
    assert '_GREENFIELD_HEAD_REVISION = "c9d0e1f2a3b4"' in content
    assert "Database migration and post-head runtime grant contract completed." in content
