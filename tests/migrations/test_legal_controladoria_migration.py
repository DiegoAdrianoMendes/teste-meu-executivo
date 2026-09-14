from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.legal_controladoria import DistributionDecision, DistributionRule, SlaEscalationLog
from app.models.legal_demand import LegalDemand

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "c2d4f6a8e0b3_add_legal_controladoria_distribution_sla.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("legal_controladoria_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "legal_demands",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))
    connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-b')"))
    connection.execute(
        sa.text("INSERT INTO legal_demands (id, workspace_id) VALUES ('demand-a', 'workspace-a')")
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_legal_controladoria_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "c2d4f6a8e0b3"
        assert migration.down_revision == "d3f7a9c1b2e4"

        migration.upgrade()

        inspector = sa.inspect(connection)
        table_names = set(inspector.get_table_names())
        assert {"distribution_rules", "distribution_decisions", "sla_escalation_logs"} <= table_names

        rule_indexes = {index["name"] for index in inspector.get_indexes("distribution_rules")}
        assert "ix_distribution_rule_workspace_id" in rule_indexes
        assert "ix_distribution_rule_active" in rule_indexes

        decision_indexes = {
            index["name"] for index in inspector.get_indexes("distribution_decisions")
        }
        assert "ix_distribution_decision_legal_demand_id" in decision_indexes

        sla_log_indexes = {
            index["name"] for index in inspector.get_indexes("sla_escalation_logs")
        }
        assert "ix_sla_escalation_log_legal_demand_id" in sla_log_indexes

        legal_demand_columns = {
            column["name"] for column in inspector.get_columns("legal_demands")
        }
        assert {
            "prazo_legal_at",
            "prazo_fatal_at",
            "prazo_interno_at",
            "sla_cliente_at",
            "prazo_playbook_at",
            "margem_seguranca_dias",
            "sla_effective_at",
            "sla_effective_layer",
            "sla_escalation_level",
        } <= legal_demand_columns

        connection.execute(
            sa.text(
                "INSERT INTO distribution_rules "
                "(id, workspace_id, name, active, priority, match_natures, match_areas, "
                "match_client_ids, match_risks, match_units, match_teams, strategy, "
                "candidate_user_ids, respect_availability, created_at, updated_at) "
                "VALUES ('rule-a', 'workspace-a', 'Regra Trabalhista', 1, 100, '[]', '[]', "
                "'[]', '[]', '[]', '[]', 'round_robin', '[\"user-a\"]', 1, "
                "'2026-07-27', '2026-07-27')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO distribution_decisions "
                "(id, workspace_id, legal_demand_id, rule_id, trigger, new_user_id, reason, "
                "decided_at, created_at, updated_at) "
                "VALUES ('decision-a', 'workspace-a', 'demand-a', 'rule-a', 'regra_automatica', "
                "'user-a', 'primeira atribuição pela regra Trabalhista', "
                "'2026-07-27', '2026-07-27', '2026-07-27')"
            )
        )
        assert connection.execute(
            sa.text("SELECT reason FROM distribution_decisions WHERE id = 'decision-a'")
        ).scalar_one() == "primeira atribuição pela regra Trabalhista"

        connection.execute(
            sa.text(
                "INSERT INTO sla_escalation_logs "
                "(id, workspace_id, legal_demand_id, level, due_at, channel_results, "
                "created_at, updated_at) "
                "VALUES ('log-a', 'workspace-a', 'demand-a', 'd3', '2026-08-01', '{}', "
                "'2026-07-27', '2026-07-27')"
            )
        )
        # Idempotência: um segundo escalonamento no MESMO nível para a
        # MESMA demanda não pode duplicar.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO sla_escalation_logs "
                    "(id, workspace_id, legal_demand_id, level, due_at, channel_results, "
                    "created_at, updated_at) "
                    "VALUES ('log-b', 'workspace-a', 'demand-a', 'd3', '2026-08-01', '{}', "
                    "'2026-07-27', '2026-07-27')"
                )
            )

        connection.execute(
            sa.text(
                "UPDATE legal_demands SET prazo_fatal_at = '2026-08-10', "
                "margem_seguranca_dias = 2, sla_effective_at = '2026-08-08', "
                "sla_effective_layer = 'prazo_fatal', sla_escalation_level = 'd7' "
                "WHERE id = 'demand-a'"
            )
        )
        assert connection.execute(
            sa.text("SELECT sla_effective_layer FROM legal_demands WHERE id = 'demand-a'")
        ).scalar_one() == "prazo_fatal"

        migration.downgrade()

        remaining = set(sa.inspect(connection).get_table_names())
        assert "distribution_rules" not in remaining
        assert "distribution_decisions" not in remaining
        assert "sla_escalation_logs" not in remaining
        legal_demand_columns_after = {
            column["name"] for column in sa.inspect(connection).get_columns("legal_demands")
        }
        assert "sla_effective_at" not in legal_demand_columns_after
    engine.dispose()


def test_legal_controladoria_models_compile_for_postgresql() -> None:
    rule_ddl = str(
        sa.schema.CreateTable(DistributionRule.__table__).compile(dialect=postgresql.dialect())
    )
    assert "UUID" in rule_ddl
    assert "distribution_strategy_enum" in rule_ddl

    decision_ddl = str(
        sa.schema.CreateTable(DistributionDecision.__table__).compile(dialect=postgresql.dialect())
    )
    assert "distribution_trigger_enum" in decision_ddl

    sla_log_ddl = str(
        sa.schema.CreateTable(SlaEscalationLog.__table__).compile(dialect=postgresql.dialect())
    )
    assert "sla_escalation_level_enum" in sla_log_ddl

    legal_demand_ddl = str(
        sa.schema.CreateTable(LegalDemand.__table__).compile(dialect=postgresql.dialect())
    )
    assert "sla_effective_at" in legal_demand_ddl
    assert "prazo_fatal_at" in legal_demand_ddl
