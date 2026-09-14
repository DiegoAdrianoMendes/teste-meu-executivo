from __future__ import annotations

import ast
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.rls import (
    COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY,
    COMMERCIAL_PORTABILITY_GUARD_TRIGGERS,
    commercial_platform_insert_policy_predicate,
    commercial_platform_read_policy_predicate,
    commercial_platform_update_policy_predicate,
)

ROOT = Path(__file__).resolve().parents[2]
REVISION = "d6a8c0e2f415"
PARENT = "c5f7a9b1d304"
MIGRATION = ROOT / ("alembic/versions/d6a8c0e2f415_expand_commercial_workflow_rls.py")
WORKFLOW_MODULES = ("sales", "customers", "finance")


def _load_migration_constants() -> SimpleNamespace:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"), filename=str(MIGRATION))
    assignments = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
    namespace: dict[str, object] = {}
    exec(
        compile(
            ast.Module(body=assignments, type_ignores=[]),
            str(MIGRATION),
            "exec",
        ),
        namespace,
    )
    return SimpleNamespace(**namespace)


def _run_alembic(database_url: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["DATABASE_URL_SYNC"] = database_url
    subprocess.run(
        [str(Path(sys.executable).with_name("alembic")), *arguments],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_revision_expands_only_canonical_workflow_tables() -> None:
    migration = _load_migration_constants()

    assert migration.revision == REVISION
    assert migration.down_revision == PARENT
    assert set(migration.WORKFLOW_CAPABILITIES) == {
        ("sales", "manage"),
        ("customers", "manage"),
        ("finance", "manage"),
    }
    assert {
        "workspace_commercial_contracts",
        "workspace_contract_lines",
        "commercial_command_receipts",
        "commercial_catalog_events",
    } == set(migration.WORKFLOW_AGGREGATE_WRITE_TABLES)
    assert migration.PORTABILITY_REBIND_CAPABILITY == (
        "commercial_portability",
        "rebind",
    )
    assert migration.PORTABILITY_RESTORE_REBIND_CAPABILITY == (
        "commercial_portability",
        "restore_rebind",
    )
    assert set(migration.PORTABILITY_TABLES) == {
        "commercial_adjustments",
        "entitlement_bonuses",
    }
    assert migration.ENTITLEMENT_VALUES == (
        *migration.PREVIOUS_ENTITLEMENT_VALUES,
        "campaign_recipients",
    )
    assert migration.ENTITLEMENT_CATALOG_TABLES == {
        "plan_entitlements": "entitlement_key_enum",
        "workspace_monthly_entitlement_usage": "entitlement_key_enum",
        "entitlement_usage_reservations": "entitlement_key_enum",
        "workspace_entitlement_overrides": "entitlement_key_enum",
        "entitlement_bonuses": "commercial_bonus_entitlement_enum",
    }
    assert migration.PORTABILITY_GUARD_TRIGGERS == (COMMERCIAL_PORTABILITY_GUARD_TRIGGERS)
    assert migration.PORTABILITY_GUARD_FUNCTION_BODY == (COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY)


def test_upgrade_and_downgrade_are_symmetric_without_recreating_policies() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "_replace_entitlement_catalog(" in source
    assert "_require_no_campaign_recipient_usage_before_downgrade()" in source
    assert "_install_portability_guard()" in source
    assert "_drop_portability_guard()" in source
    assert "BEFORE UPDATE ON" in source
    assert "SECURITY INVOKER" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "function_namespace.nspname" in source
    assert "trigger.tgenabled::text" in source
    assert "trigger.tgtype" in source
    assert "trigger.tgqual IS NULL" in source
    assert "trigger.tgattr::text" in source
    assert "trigger.tgnargs" in source
    assert "pg_get_triggerdef(trigger.oid)" in source
    assert '("trigger_type", int(trigger_type), 19)' in source
    assert "_expected_trigger_definition(" in source
    assert "_normalized_trigger_definition(" in source
    assert "_install(forward=True)" in source
    assert "_install(forward=False)" in source
    assert "ALTER POLICY" in source
    assert "DROP POLICY" not in source
    assert "CREATE POLICY" not in source


def test_runtime_predicates_match_the_forward_policy_contract() -> None:
    for workflow_module in WORKFLOW_MODULES:
        capability = f"{workflow_module}:manage"
        for table_name in (
            "commercial_offers",
            "commercial_offer_versions",
            "workspace_commercial_contracts",
            "workspace_contract_lines",
            "commercial_adjustments",
            "entitlement_bonuses",
            "commercial_command_receipts",
            "commercial_catalog_events",
        ):
            assert capability in commercial_platform_read_policy_predicate(table_name)
        for table_name in (
            "workspace_commercial_contracts",
            "workspace_contract_lines",
            "commercial_command_receipts",
            "commercial_catalog_events",
        ):
            assert capability in commercial_platform_insert_policy_predicate(table_name)
        for table_name in (
            "workspace_commercial_contracts",
            "workspace_contract_lines",
        ):
            assert capability in commercial_platform_update_policy_predicate(table_name)
        for table_name in ("commercial_adjustments", "entitlement_bonuses"):
            assert capability not in commercial_platform_insert_policy_predicate(table_name)
            assert capability not in commercial_platform_update_policy_predicate(table_name)
        assert capability not in commercial_platform_insert_policy_predicate("commercial_offers")
        assert capability not in commercial_platform_insert_policy_predicate(
            "commercial_offer_versions"
        )

    for table_name in ("commercial_adjustments", "entitlement_bonuses"):
        read_predicate = commercial_platform_read_policy_predicate(table_name)
        insert_predicate = commercial_platform_insert_policy_predicate(table_name)
        update_predicate = commercial_platform_update_policy_predicate(table_name)
        assert "commercial_portability:rebind" in read_predicate
        assert "commercial_portability:rebind" not in insert_predicate
        assert "commercial_portability:rebind" in update_predicate

    adjustment_read = commercial_platform_read_policy_predicate("commercial_adjustments")
    adjustment_insert = commercial_platform_insert_policy_predicate("commercial_adjustments")
    adjustment_update = commercial_platform_update_policy_predicate("commercial_adjustments")
    assert "commercial_portability:restore_rebind" in adjustment_read
    assert "commercial_portability:restore_rebind" not in adjustment_insert
    assert "commercial_portability:restore_rebind" in adjustment_update
    assert "commercial_portability:restore_rebind" not in (
        commercial_platform_read_policy_predicate("entitlement_bonuses")
    )
    assert "commercial_portability:restore_rebind" not in (
        commercial_platform_update_policy_predicate("entitlement_bonuses")
    )


def test_sqlite_revision_graph_round_trips_entitlement_catalog_fail_closed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "commercial-workflow-rls.db"
    database_url = f"sqlite:///{database_path}"
    migration = _load_migration_constants()
    previous_allowed = ", ".join(f"'{value}'" for value in migration.PREVIOUS_ENTITLEMENT_VALUES)
    connection = sqlite3.connect(database_path)
    try:
        for table_name, constraint_name in migration.ENTITLEMENT_CATALOG_TABLES.items():
            connection.execute(
                f'CREATE TABLE "{table_name}" ('
                "id INTEGER PRIMARY KEY, "
                f"entitlement VARCHAR({migration.PREVIOUS_ENTITLEMENT_LENGTH}) "
                "NOT NULL, "
                f'CONSTRAINT "{constraint_name}" '
                f"CHECK (entitlement IN ({previous_allowed})))"
            )
        connection.commit()
    finally:
        connection.close()

    _run_alembic(database_url, "stamp", PARENT)
    _run_alembic(database_url, "upgrade", REVISION)
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            REVISION,
        )
        for table_name in migration.ENTITLEMENT_CATALOG_TABLES:
            connection.execute(
                f'INSERT INTO "{table_name}" (entitlement) VALUES (?)',
                ("campaign_recipients",),
            )
        connection.commit()
    finally:
        connection.close()

    failed_downgrade = subprocess.run(
        [str(Path(sys.executable).with_name("alembic")), "downgrade", PARENT],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            "DATABASE_URL_SYNC": database_url,
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed_downgrade.returncode != 0
    assert "Cannot downgrade campaign_recipients" in (
        failed_downgrade.stdout + failed_downgrade.stderr
    )

    connection = sqlite3.connect(database_path)
    try:
        for table_name in migration.ENTITLEMENT_CATALOG_TABLES:
            connection.execute(
                f'DELETE FROM "{table_name}" WHERE entitlement = ?',
                ("campaign_recipients",),
            )
        connection.commit()
    finally:
        connection.close()

    _run_alembic(database_url, "downgrade", PARENT)
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (PARENT,)
        for table_name in migration.ENTITLEMENT_CATALOG_TABLES:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    f'INSERT INTO "{table_name}" (entitlement) VALUES (?)',
                    ("campaign_recipients",),
                )
    finally:
        connection.close()
