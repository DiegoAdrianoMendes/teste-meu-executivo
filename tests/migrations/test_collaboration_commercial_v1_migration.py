from __future__ import annotations

import ast
import os
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.db.rls import (
    COMMERCIAL_CONTROL_TABLES,
    PLATFORM_TASK_CONTROL_TABLES,
    TASK_REMINDER_DIRECT_TENANT_TABLES,
)

MIGRATION = (
    Path(__file__).parents[2]
    / "alembic/versions/c8e0f2a4b615_add_v1_collaboration_and_commercial_control.py"
)
PROJECT_ROOT = MIGRATION.parents[2]
TASK_ORGANIZATION_TABLES = {
    "task_lists",
    "task_list_memberships",
    "task_boards",
    "task_board_columns",
    "task_board_placements",
}


def _literal_assignment(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
    raise AssertionError(f"assignment {name!r} not found")


def _load_migration_function(name: str):
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = {"UTC": UTC, "datetime": datetime, "ZoneInfo": ZoneInfo}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(MIGRATION), "exec"), namespace)
    return namespace[name]


def test_collaboration_commercial_migration_is_one_forward_revision() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert _literal_assignment(tree, "revision") == "c8e0f2a4b615"
    assert _literal_assignment(tree, "down_revision") == "b71d0e4c9f52"

    for table_name in (
        *COMMERCIAL_CONTROL_TABLES,
        *PLATFORM_TASK_CONTROL_TABLES,
        "tasks",
        "task_events",
        "task_participants",
        "task_review_rounds",
        *TASK_REMINDER_DIRECT_TENANT_TABLES,
        "meeting_notification_outbox",
        "meeting_participants",
    ):
        assert f'"{table_name}"' in source

    organization_source = (
        PROJECT_ROOT / "alembic/versions/za8t1q4r5m97_add_task_organization.py"
    ).read_text(encoding="utf-8")
    for table_name in TASK_ORGANIZATION_TABLES:
        assert f'"{table_name}"' in organization_source
        assert '_enable_rls(table)' in organization_source

    client_contract_rls_source = (
        PROJECT_ROOT / "alembic/versions/zb9u2r5s6n08_enforce_client_contracts_rls.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "zb9u2r5s6n08"' in client_contract_rls_source
    assert 'down_revision = "za8t1q4r5m97"' in client_contract_rls_source
    assert '_TABLE = "client_contracts"' in client_contract_rls_source
    assert "ENABLE ROW LEVEL SECURITY" in client_contract_rls_source
    assert "FORCE ROW LEVEL SECURITY" in client_contract_rls_source
    assert "current_workspace_id" in client_contract_rls_source

    assert re.search(r'op\.add_column\(\s*"tasks"', source)
    assert re.search(r'op\.add_column\(\s*"meetings"', source)
    assert "assigned_to" in source
    assert "legacy_due_text" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "current_platform_actor_id" in source
    assert "current_workspace_id" in source
    assert '"storage_objects"' in source
    assert '"idempotency_key_hash"' in source
    assert '"ck_storage_object_idempotency_key_hash_length"' in source
    assert '"uq_storage_object_workspace_idempotency_key"' in source
    storage_expand = source[
        source.index("def _expand_private_storage_idempotency") : source.index(
            "def _contract_private_storage_idempotency"
        )
    ]
    assert 'with op.batch_alter_table("storage_objects")' in storage_expand


def test_append_only_evidence_tables_have_database_guards() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    immutable_tables = _literal_assignment(tree, "IMMUTABLE_TABLES")
    for table_name in (
        "commercial_catalog_events",
        "commercial_command_receipts",
        "commercial_offers",
        "commercial_offer_versions",
        "meeting_creation_receipts",
        "platform_task_events",
        "task_events",
        "task_reminder_command_receipts",
        "task_reminder_events",
    ):
        assert table_name in immutable_tables
    assert "trg_{table_name}_immutable" in source
    assert "BEFORE UPDATE OR DELETE" in source
    assert "BEFORE TRUNCATE" in source


def test_downgrade_is_explicit_and_drops_children_before_parents() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "def downgrade() -> None:" in source
    downgrade = source[source.index("def downgrade() -> None:") :]
    assert downgrade.index(
        "_require_no_non_reconstructible_v1_data_before_downgrade()"
    ) < downgrade.index("_require_legacy_entitlement_values_before_downgrade()")
    assert "was refused before any DROP" in source
    assert source.index('op.drop_table("task_events")') < source.index(
        'op.drop_constraint("uq_tasks_matter_id_id"'
    )
    assert source.index('op.drop_table("workspace_contract_lines")') < source.index(
        'op.drop_table("workspace_commercial_contracts")'
    )
    storage_contract = source[source.index("def _contract_private_storage_idempotency") :]
    assert storage_contract.index("op.drop_index") < storage_contract.index(
        "op.drop_constraint"
    ) < storage_contract.index("op.drop_column")


def test_task_reminder_tenant_bindings_and_drop_order_are_explicit() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    for constraint_name in (
        "uq_task_reminder_rules_tenant_binding",
        "fk_task_reminder_rules_workspace_matter",
        "fk_task_reminder_rules_matter_task",
        "fk_task_reminder_outbox_tenant_rule",
        "uq_task_reminder_outbox_tenant_binding",
        "fk_task_reminder_events_workspace_matter",
        "fk_task_reminder_events_tenant_rule",
        "fk_task_reminder_events_tenant_outbox",
        "fk_task_reminder_receipts_workspace_matter",
        "fk_task_reminder_receipts_matter_task",
        "fk_task_reminder_receipts_tenant_rule",
    ):
        assert constraint_name in source

    drop_reminders = source[source.index("def _drop_task_reminder_tables") :]
    assert drop_reminders.index(
        'op.drop_table("task_reminder_command_receipts")'
    ) < drop_reminders.index('op.drop_table("task_reminder_events")')
    assert drop_reminders.index('op.drop_table("task_reminder_events")') < (
        drop_reminders.index('op.drop_table("task_reminder_outbox")')
    )
    assert drop_reminders.index('op.drop_table("task_reminder_outbox")') < (
        drop_reminders.index('op.drop_table("task_reminder_rules")')
    )


def test_legacy_task_backfill_never_invents_owner_authorship_or_due_time() -> None:
    parse_legacy_due_date = _load_migration_function("_parse_legacy_due_date")
    source = MIGRATION.read_text(encoding="utf-8")

    assert parse_legacy_due_date("2026-07-30") is None
    assert parse_legacy_due_date("2026-07-30T14:30:00") is None
    assert parse_legacy_due_date("prazo a confirmar") is None
    assert parse_legacy_due_date("2026-07-30T14:30:00Z") == datetime(
        2026, 7, 30, 14, 30, tzinfo=UTC
    )
    backfill = source[source.index("def _backfill_legacy_tasks") : source.index(
        "def _backfill_meeting_organizers"
    )]
    assert "responsible_human_id" not in backfill
    assert "LIMIT 1" not in backfill
    assert "created_by_user_id, created_at" in backfill
    assert "true, NULL," in backfill
    assert "task_created_at" in backfill
    assert "datetime.now" not in backfill
    assert "Task collaboration preflight found tasks without one valid active owner" in backfill
    assert "if owner_user_id is None" not in backfill
    meeting_backfill = source[
        source.index("def _backfill_meeting_organizers") : source.index(
            "def _migration_uuid"
        )
    ]
    assert "Meeting collaboration preflight found responsible users" in meeting_backfill
    assert "meeting_created_at" in meeting_backfill
    assert "datetime.now" not in meeting_backfill


def test_v1_database_checks_are_present_in_the_published_migration() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for constraint_name in (
        "ck_platform_tasks_review_round_nonnegative",
        "ck_platform_tasks_reviewer_distinct_owner",
        "ck_platform_tasks_review_contract",
        "ck_platform_tasks_owner_deadline_within_task",
        "ck_platform_tasks_review_deadline_within_task",
        "ck_platform_task_participants_required_assignee",
        "ck_platform_task_participants_observer_without_deadline",
        "ck_platform_task_participants_lifecycle",
        "ck_platform_task_review_round_number_positive",
        "ck_platform_task_review_version_positive",
        "ck_platform_task_review_resolution",
        "ck_commercial_offer_currency_brl",
        "ck_workspace_contract_currency_brl",
        "ck_commercial_adjustment_percentage_bps",
        "ck_commercial_adjustment_remaining_cycles_nonnegative",
    ):
        assert constraint_name in source

def _run_real_alembic(sqlite_url: str, *arguments: str) -> None:
    executable = Path(sys.executable).with_name("alembic")
    assert executable.exists(), f"Alembic executable not found next to {sys.executable}"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = sqlite_url
    environment["DATABASE_URL_SYNC"] = sqlite_url
    result = subprocess.run(
        [str(executable), *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _create_sqlite_pre_v1_baseline(database_path: Path) -> None:
    """Create only the authoritative parents c8 expands or references.

    Historical revision ``1ab66505e05b`` contains PostgreSQL-only defaults, so
    a base-to-head SQLite run is not a supported historical contract.  This
    fixture instead materializes the published b71 boundary and exercises c8
    itself through the real Alembic CLI in both directions.
    """

    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE workspaces (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE users (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(240),
                agent_type VARCHAR(40),
                is_active BOOLEAN NOT NULL DEFAULT 1
            );
            CREATE TABLE matters (
                id VARCHAR(36) PRIMARY KEY,
                workspace_id VARCHAR(36) NOT NULL,
                UNIQUE(workspace_id, id),
                UNIQUE(id, workspace_id)
            );
            CREATE TABLE workspace_memberships (
                id VARCHAR(36) PRIMARY KEY,
                workspace_id VARCHAR(36) NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                status VARCHAR(40) NOT NULL,
                UNIQUE(id, workspace_id, user_id)
            );
            CREATE TABLE tasks (
                id VARCHAR(36) PRIMARY KEY,
                matter_id VARCHAR(36) NOT NULL,
                assigned_to VARCHAR(36),
                title VARCHAR(500),
                due_date VARCHAR(240),
                status VARCHAR(40) NOT NULL DEFAULT 'pending',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE meetings (
                id VARCHAR(36) PRIMARY KEY,
                workspace_id VARCHAR(36) NOT NULL,
                matter_id VARCHAR(36),
                responsible_user_id VARCHAR(36),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE storage_objects (
                id VARCHAR(36) PRIMARY KEY,
                workspace_id VARCHAR(36) NOT NULL
            );
            CREATE TABLE support_tickets (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE platform_incidents (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE growth_prospects (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE platform_releases (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE administrative_sessions (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE support_access_grants (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE notifications (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE plan_entitlements (
                id VARCHAR(36) PRIMARY KEY,
                entitlement VARCHAR(19) NOT NULL,
                CONSTRAINT entitlement_key_enum CHECK (
                    entitlement IN (
                        'tokens', 'ai_executions', 'ai_conversations',
                        'audio_minutes', 'storage_bytes', 'files', 'users',
                        'whatsapp_numbers', 'agents', 'automations',
                        'digital_signature', 'calendar', 'email', 'webhooks',
                        'advanced_audit', 'success_manager', 'legal_areas',
                        'monitored_processes'
                    )
                )
            );
            CREATE TABLE workspace_monthly_entitlement_usage (
                id VARCHAR(36) PRIMARY KEY,
                entitlement VARCHAR(19) NOT NULL,
                CONSTRAINT entitlement_key_enum CHECK (
                    entitlement IN (
                        'tokens', 'ai_executions', 'ai_conversations',
                        'audio_minutes', 'storage_bytes', 'files', 'users',
                        'whatsapp_numbers', 'agents', 'automations',
                        'digital_signature', 'calendar', 'email', 'webhooks',
                        'advanced_audit', 'success_manager', 'legal_areas',
                        'monitored_processes'
                    )
                )
            );
            CREATE TABLE entitlement_usage_reservations (
                id VARCHAR(36) PRIMARY KEY,
                entitlement VARCHAR(19) NOT NULL,
                CONSTRAINT entitlement_key_enum CHECK (
                    entitlement IN (
                        'tokens', 'ai_executions', 'ai_conversations',
                        'audio_minutes', 'storage_bytes', 'files', 'users',
                        'whatsapp_numbers', 'agents', 'automations',
                        'digital_signature', 'calendar', 'email', 'webhooks',
                        'advanced_audit', 'success_manager', 'legal_areas',
                        'monitored_processes'
                    )
                )
            );
            CREATE TABLE workspace_entitlement_overrides (
                id VARCHAR(36) PRIMARY KEY,
                entitlement VARCHAR(19) NOT NULL,
                CONSTRAINT entitlement_key_enum CHECK (
                    entitlement IN (
                        'tokens', 'ai_executions', 'ai_conversations',
                        'audio_minutes', 'storage_bytes', 'files', 'users',
                        'whatsapp_numbers', 'agents', 'automations',
                        'digital_signature', 'calendar', 'email', 'webhooks',
                        'advanced_audit', 'success_manager', 'legal_areas',
                        'monitored_processes'
                    )
                )
            );
            """
        )
    finally:
        connection.close()


def test_sqlite_real_alembic_refuses_data_loss_then_round_trips_empty_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "v1-round-trip.db"
    sqlite_url = f"sqlite:///{database_path}"
    _create_sqlite_pre_v1_baseline(database_path)

    _run_real_alembic(sqlite_url, "stamp", "b71d0e4c9f52")
    _run_real_alembic(sqlite_url, "upgrade", "c8e0f2a4b615")

    event_id = "00000000-0000-0000-0000-000000000001"
    connection = sqlite3.connect(database_path)
    try:
        for index, table_name in enumerate(
            (
                "plan_entitlements",
                "workspace_monthly_entitlement_usage",
                "entitlement_usage_reservations",
                "workspace_entitlement_overrides",
            ),
            start=1,
        ):
            connection.execute(
                f'INSERT INTO "{table_name}" (id, entitlement) VALUES (?, ?)',
                (f"00000000-0000-0000-0000-{index:012d}", "meeting_participants"),
            )
            declared_type = next(
                row[2]
                for row in connection.execute(f'PRAGMA table_info("{table_name}")')
                if row[1] == "entitlement"
            )
            assert declared_type == "VARCHAR(21)"
            connection.execute(f'DELETE FROM "{table_name}"')
        connection.execute(
            "INSERT INTO commercial_catalog_events "
            "(event_type, resource_type, reason, correlation_id, id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("created", "offer", "prova append-only", "sqlite-c8", event_id),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="is immutable"):
            connection.execute(
                "UPDATE commercial_catalog_events SET reason = ? WHERE id = ?",
                ("mutated", event_id),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="is immutable"):
            connection.execute(
                "DELETE FROM commercial_catalog_events WHERE id = ?",
                (event_id,),
            )
        connection.rollback()
    finally:
        connection.close()

    executable = Path(sys.executable).with_name("alembic")
    environment = {
        **os.environ,
        "DATABASE_URL": sqlite_url,
        "DATABASE_URL_SYNC": sqlite_url,
    }
    refused = subprocess.run(
        [str(executable), "downgrade", "b71d0e4c9f52"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode != 0
    assert "was refused before any DROP" in refused.stdout + refused.stderr

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "c8e0f2a4b615",
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM commercial_catalog_events"
        ).fetchone() == (1,)
        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = 'commercial_catalog_events'"
            )
        }
        assert trigger_names == {
            "trg_commercial_catalog_events_immutable_update",
            "trg_commercial_catalog_events_immutable_delete",
        }
    finally:
        connection.close()

    empty_database_path = tmp_path / "v1-empty-round-trip.db"
    empty_sqlite_url = f"sqlite:///{empty_database_path}"
    _create_sqlite_pre_v1_baseline(empty_database_path)
    _run_real_alembic(empty_sqlite_url, "stamp", "b71d0e4c9f52")
    _run_real_alembic(empty_sqlite_url, "upgrade", "c8e0f2a4b615")
    _run_real_alembic(empty_sqlite_url, "downgrade", "b71d0e4c9f52")
    _run_real_alembic(empty_sqlite_url, "upgrade", "c8e0f2a4b615")
