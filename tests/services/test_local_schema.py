import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.exc import DBAPIError

from app.db.local_schema import ensure_local_sqlite_schema
from app.models.agent_platform import AgentDeployment


def test_local_schema_adds_blog_post_revision_idempotently():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "blog_posts",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("title", String(220), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO blog_posts (id, title) VALUES ('p1', 'Legado')")
        )
        ensure_local_sqlite_schema(connection)
        ensure_local_sqlite_schema(connection)
        revision = connection.execute(
            text("SELECT revision FROM blog_posts WHERE id = 'p1'")
        ).scalar_one()

    columns = {column["name"] for column in inspect(engine).get_columns("blog_posts")}
    assert "revision" in columns
    assert revision == 1


def test_local_schema_adds_effective_dated_tax_profile_contract_idempotently():
    """An existing QA database must support the current export snapshot model."""
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "finance_tax_profiles",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("workspace_id", String(36), nullable=False),
        Column("regime", String(40), nullable=False),
        Column("tax_rate", String(30), nullable=False),
        Column("effective_from", String(10), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO finance_tax_profiles "
                "(id, workspace_id, regime, tax_rate, effective_from) "
                "VALUES ('profile-1', 'workspace-1', 'lucro_real', '0', '2026-01-01')"
            )
        )
        ensure_local_sqlite_schema(connection)
        ensure_local_sqlite_schema(connection)
        row = connection.execute(
            text(
                "SELECT id, regime, effective_from, effective_until "
                "FROM finance_tax_profiles WHERE id = 'profile-1'"
            )
        ).mappings().one()

    columns = {
        column["name"] for column in inspect(engine).get_columns("finance_tax_profiles")
    }
    indexes = {index["name"] for index in inspect(engine).get_indexes("finance_tax_profiles")}
    assert "effective_until" in columns
    assert "ix_finance_tax_profiles_workspace_effective" in indexes
    assert row == {
        "id": "profile-1",
        "regime": "lucro_real",
        "effective_from": "2026-01-01",
        "effective_until": None,
    }


def test_local_schema_upgrades_legacy_agent_runs_v2_columns_and_indexes_idempotently():
    """A pre-V2 local AgentRun table must not make agent-run reads fail."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE agent_runs (
                    id CHAR(36) PRIMARY KEY,
                    workspace_id CHAR(36) NOT NULL,
                    matter_id CHAR(36),
                    agent_name VARCHAR(120) NOT NULL,
                    trigger_source VARCHAR(120) NOT NULL,
                    status VARCHAR(32) NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, workspace_id, agent_name, trigger_source, status) "
                "VALUES ('run-1', 'workspace-1', 'finance', 'manual', 'queued')"
            )
        )
        before_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(agent_runs)"))
        }
        assert {"context_type", "context_id", "deployment_id"}.isdisjoint(before_columns)
        with pytest.raises(DBAPIError, match="no such column: context_type"):
            connection.execute(text("SELECT context_type FROM agent_runs")).all()

        ensure_local_sqlite_schema(connection)
        ensure_local_sqlite_schema(connection)

        after_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(agent_runs)"))
        }
        after_indexes = {
            row[1] for row in connection.execute(text("PRAGMA index_list(agent_runs)"))
        }
        run = connection.execute(
            text(
                "SELECT id, workspace_id, context_type, context_id, deployment_id, "
                "triggered_by_user_id FROM agent_runs WHERE id = 'run-1'"
            )
        ).mappings().one()

    assert {"context_type", "context_id", "deployment_id", "triggered_by_user_id"}.issubset(
        after_columns
    )
    assert {
        "ix_agent_runs_context_type",
        "ix_agent_runs_context_id",
        "ix_agent_runs_deployment_id",
        "ix_agent_runs_triggered_by_user_id",
    }.issubset(after_indexes)
    assert run == {
        "id": "run-1",
        "workspace_id": "workspace-1",
        "context_type": None,
        "context_id": None,
        "deployment_id": None,
        "triggered_by_user_id": None,
    }


def test_local_schema_upgrades_legacy_agent_deployment_v3_bindings_idempotently():
    """Local V3 deployment tables receive every later additive model field."""
    engine = create_engine("sqlite:///:memory:")
    legacy_columns = {
        "id",
        "workspace_id",
        "agent_key",
        "version",
        "status",
        "config",
        "gates",
        "created_at",
        "updated_at",
    }
    expected_additions = {
        "role_package",
        "tool_bindings",
        "knowledge_bindings",
        "autonomy_policy",
        "human_routing_policy",
        "budget_policy",
    }
    assert expected_additions == set(AgentDeployment.__table__.columns.keys()) - legacy_columns

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE agent_deployments (
                    id CHAR(36) PRIMARY KEY,
                    workspace_id CHAR(36) NOT NULL,
                    agent_key VARCHAR(160) NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    status VARCHAR(20) NOT NULL DEFAULT 'draft',
                    config JSON NOT NULL DEFAULT '{}',
                    gates JSON NOT NULL DEFAULT '{}',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_agent_deployments_key_version
                        UNIQUE (workspace_id, agent_key, version)
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO agent_deployments "
                "(id, workspace_id, agent_key, created_at, updated_at) VALUES "
                "('deployment-1', 'workspace-1', 'finance', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        before_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(agent_deployments)"))
        }
        assert expected_additions.isdisjoint(before_columns)

        ensure_local_sqlite_schema(connection)
        ensure_local_sqlite_schema(connection)

        after_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(agent_deployments)"))
        }
        after_indexes = {
            row[1] for row in connection.execute(text("PRAGMA index_list(agent_deployments)"))
        }
        deployment = connection.execute(
            text(
                "SELECT role_package, tool_bindings, knowledge_bindings, autonomy_policy, "
                "human_routing_policy, budget_policy FROM agent_deployments "
                "WHERE id = 'deployment-1'"
            )
        ).mappings().one()

    assert expected_additions.issubset(after_columns)
    assert "ix_agent_deployments_role_package" in after_indexes
    assert deployment == {
        "role_package": "legal_operations",
        "tool_bindings": "[]",
        "knowledge_bindings": "[]",
        "autonomy_policy": "{}",
        "human_routing_policy": "{}",
        "budget_policy": "{}",
    }


def test_local_schema_upgrade_preserves_existing_memberships_and_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "workspace_memberships",
        metadata,
        Column("id", String, primary_key=True),
        Column("user_id", String, nullable=False),
        Column("workspace_id", String, nullable=False),
        Column("role", String, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspace_memberships (id, user_id, workspace_id, role) "
                "VALUES ('m1', 'u1', 'w1', 'workspace_owner')"
            )
        )
        ensure_local_sqlite_schema(connection)
        ensure_local_sqlite_schema(connection)
        row = (
            connection.execute(
                text(
                    "SELECT id, access_package, permission_grants, permission_denials "
                    "FROM workspace_memberships WHERE id = 'm1'"
                )
            )
            .mappings()
            .one()
        )

    columns = {column["name"] for column in inspect(engine).get_columns("workspace_memberships")}
    assert {"access_package", "permission_grants", "permission_denials"}.issubset(columns)
    assert row == {
        "id": "m1",
        "access_package": "office_owner",
        "permission_grants": "[]",
        "permission_denials": "[]",
    }


def test_local_schema_refreshes_entitlement_enum_constraints_without_losing_rows():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE plan_entitlements (
                id VARCHAR(36) PRIMARY KEY,
                entitlement VARCHAR(80) NOT NULL,
                CONSTRAINT entitlement_key_enum
                    CHECK (entitlement IN ('tokens', 'users'))
            )
            """
        )
        connection.execute(
            text(
                "INSERT INTO plan_entitlements (id, entitlement) "
                "VALUES ('existing', 'tokens')"
            )
        )

        ensure_local_sqlite_schema(connection)
        ensure_local_sqlite_schema(connection)
        connection.execute(
            text(
                "INSERT INTO plan_entitlements (id, entitlement) VALUES "
                "('legal', 'legal_areas'), ('monitoring', 'monitored_processes')"
            )
        )
        values = list(
            connection.execute(
                text("SELECT entitlement FROM plan_entitlements ORDER BY id")
            ).scalars()
        )

    assert values == ["tokens", "legal_areas", "monitored_processes"]


def test_local_schema_adds_runtime_hardening_columns_and_indexes_idempotently():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "users",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("email", String(255), nullable=False),
    )
    Table(
        "password_reset_tokens",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("user_id", String(36), nullable=False),
    )
    Table(
        "subscriptions",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("stripe_subscription_id", String(120)),
        Column("stripe_customer_id", String(120)),
    )
    Table(
        "scheduled_messages",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("status", String(32), nullable=False),
    )
    Table(
        "whatsapp_messages",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("body", Text, nullable=False),
    )
    Table(
        "oss_external_entity_links",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("provider", String(32), nullable=False),
        Column("external_account_id", String(255), nullable=False),
        Column("klm_entity_type", String(100), nullable=False),
        Column("external_entity_type", String(100), nullable=False),
        Column("sync_state", String(32), nullable=False),
    )
    Table(
        "knowledge_chunks",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("content_text", Text, nullable=False),
    )
    Table(
        "matters",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("workspace_id", String(36), nullable=False),
        Column("practice_area", String(80), nullable=True),
    )
    Table(
        "klm_omnichannel_threads",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("workspace_id", String(36), nullable=False),
        Column("status", String(32), nullable=False),
        Column("last_message_at", DateTime),
    )
    Table(
        "klm_omnichannel_messages",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("workspace_id", String(36), nullable=False),
        Column("thread_id", String(36), nullable=False),
        Column("body", Text, nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email) VALUES ('user-1', 'legacy@example.test')")
        )
        connection.execute(
            text("INSERT INTO password_reset_tokens (id, user_id) VALUES ('reset-1', 'user-1')")
        )
        connection.execute(text("INSERT INTO subscriptions (id) VALUES ('subscription-1')"))
        connection.execute(
            text("INSERT INTO scheduled_messages (id, status) VALUES ('scheduled-1', 'pending')")
        )
        connection.execute(
            text("INSERT INTO whatsapp_messages (id, body) VALUES ('message-1', 'Olá')")
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_chunks (id, content_text) "
                "VALUES ('chunk-1', 'Documento legado')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO matters (id, workspace_id, practice_area) "
                "VALUES ('matter-1', 'workspace-1', 'health_plan')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO klm_omnichannel_threads "
                "(id, workspace_id, status) VALUES ('thread-1', 'workspace-1', 'open')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO klm_omnichannel_messages "
                "(id, workspace_id, thread_id, body) "
                "VALUES ('message-1', 'workspace-1', 'thread-1', 'Mensagem legada')"
            )
        )
        ensure_local_sqlite_schema(connection)
        ensure_local_sqlite_schema(connection)

        scheduled = (
            connection.execute(
                text(
                    "SELECT delivery_attempt_count, provider_supports_idempotency "
                    "FROM scheduled_messages WHERE id = 'scheduled-1'"
                )
            )
            .mappings()
            .one()
        )
        inbound_runtime = (
            connection.execute(
                text(
                    "SELECT inbound_pipeline, inbound_lease_token, "
                    "inbound_lease_expires_at, inbound_lease_generation "
                    "FROM whatsapp_messages "
                    "WHERE id = 'message-1'"
                )
            )
            .mappings()
            .one()
        )
        identity_runtime = (
            connection.execute(
                text(
                    "SELECT email_verified_at, auth_epoch, platform_role_package, "
                    "platform_access_active, platform_permission_grants, "
                    "platform_permission_denials FROM users WHERE id = 'user-1'"
                )
            )
            .mappings()
            .one()
        )
        password_reset_runtime = (
            connection.execute(
                text(
                    "SELECT code_hash, last_delivery_id, revoked_at "
                    "FROM password_reset_tokens WHERE id = 'reset-1'"
                )
            )
            .mappings()
            .one()
        )
        subscription_runtime = (
            connection.execute(
                text("SELECT cancel_at_period_end FROM subscriptions WHERE id = 'subscription-1'")
            )
            .mappings()
            .one()
        )
        knowledge_runtime = (
            connection.execute(
                text(
                    "SELECT content_text, embedding_data FROM knowledge_chunks WHERE id = 'chunk-1'"
                )
            )
            .mappings()
            .one()
        )
        matter_runtime = (
            connection.execute(
                text(
                    "SELECT tags, legal_area_key, legal_product_key, legal_product_custom, "
                    "legal_object_name, counterparty_name, legal_issue_summary, legal_context, "
                    "health_subtheme_custom, manual_context_revision, "
                    "manual_context_updated_at FROM matters WHERE id = 'matter-1'"
                )
            )
            .mappings()
            .one()
        )

    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    password_reset_columns = {
        column["name"] for column in inspect(engine).get_columns("password_reset_tokens")
    }
    subscription_columns = {
        column["name"] for column in inspect(engine).get_columns("subscriptions")
    }
    scheduled_columns = {
        column["name"] for column in inspect(engine).get_columns("scheduled_messages")
    }
    whatsapp_columns = {
        column["name"] for column in inspect(engine).get_columns("whatsapp_messages")
    }
    knowledge_columns = {
        column["name"] for column in inspect(engine).get_columns("knowledge_chunks")
    }
    matter_columns = {column["name"] for column in inspect(engine).get_columns("matters")}
    thread_columns = {
        column["name"] for column in inspect(engine).get_columns("klm_omnichannel_threads")
    }
    message_columns = {
        column["name"] for column in inspect(engine).get_columns("klm_omnichannel_messages")
    }
    scheduled_indexes = {
        index["name"] for index in inspect(engine).get_indexes("scheduled_messages")
    }
    subscription_indexes = {index["name"] for index in inspect(engine).get_indexes("subscriptions")}
    whatsapp_indexes = {index["name"] for index in inspect(engine).get_indexes("whatsapp_messages")}
    oss_binding_indexes = {
        index["name"] for index in inspect(engine).get_indexes("oss_external_entity_links")
    }
    thread_indexes = {
        index["name"] for index in inspect(engine).get_indexes("klm_omnichannel_threads")
    }
    matter_indexes = {index["name"] for index in inspect(engine).get_indexes("matters")}

    assert {"email_verified_at", "auth_epoch"}.issubset(user_columns)
    assert {
        "platform_role_package",
        "platform_access_active",
        "platform_permission_grants",
        "platform_permission_denials",
    }.issubset(user_columns)
    assert {"code_hash", "last_delivery_id", "revoked_at"}.issubset(password_reset_columns)
    assert {
        "last_stripe_event_id",
        "last_stripe_event_created",
        "cancel_at_period_end",
    }.issubset(subscription_columns)
    assert {
        "idempotency_key",
        "lease_token",
        "lease_expires_at",
        "delivery_attempted_at",
        "delivery_attempt_count",
        "provider_supports_idempotency",
        "provider_message_id",
        "delivery_uncertain_at",
    }.issubset(scheduled_columns)
    assert {
        "inbound_pipeline",
        "inbound_lease_token",
        "inbound_lease_expires_at",
        "inbound_lease_generation",
        "outbound_idempotency_key",
        "outbound_payload_digest",
        "outbound_request_started_at",
        "delivery_uncertain_at",
        "outbound_error",
    }.issubset(whatsapp_columns)
    assert "embedding_data" in knowledge_columns
    assert {
        "tags",
        "legal_area_key",
        "legal_product_key",
        "legal_product_custom",
        "legal_object_name",
        "counterparty_name",
        "legal_issue_summary",
        "legal_context",
        "health_subtheme_custom",
        "manual_context_revision",
        "manual_context_updated_at",
    }.issubset(matter_columns)
    assert {
        "identity_id",
        "assigned_team_id",
        "owning_agent_key",
        "owning_area_key",
        "owner_since",
        "owner_generation",
        "queue",
        "priority",
        "is_favorite",
        "preferred_channel",
        "version",
        "last_inbound_at",
        "last_outbound_at",
        "first_response_due_at",
        "resolution_due_at",
        "closed_at",
        "closed_reason",
        "policy_state",
        "cost_state",
    }.issubset(thread_columns)
    assert {
        "endpoint_id",
        "binding_id",
        "audience",
        "content_type",
        "language",
        "provider_status",
        "transport",
        "cost_amount",
        "cost_currency",
        "window_expires_at",
        "template_id",
    }.issubset(message_columns)
    assert scheduled == {
        "delivery_attempt_count": 0,
        "provider_supports_idempotency": 0,
    }
    assert inbound_runtime == {
        "inbound_pipeline": "{}",
        "inbound_lease_token": None,
        "inbound_lease_expires_at": None,
        "inbound_lease_generation": 0,
    }
    assert identity_runtime == {
        "email_verified_at": None,
        "auth_epoch": 0,
        "platform_role_package": None,
        "platform_access_active": 0,
        "platform_permission_grants": "[]",
        "platform_permission_denials": "[]",
    }
    assert password_reset_runtime == {
        "code_hash": None,
        "last_delivery_id": None,
        "revoked_at": None,
    }
    assert subscription_runtime == {"cancel_at_period_end": 0}
    assert knowledge_runtime == {
        "content_text": "Documento legado",
        "embedding_data": "[]",
    }
    assert matter_runtime == {
        "tags": "[]",
        "legal_area_key": "health_plan",
        "legal_product_key": None,
        "legal_product_custom": None,
        "legal_object_name": None,
        "counterparty_name": None,
        "legal_issue_summary": None,
        "legal_context": "{}",
        "health_subtheme_custom": None,
        "manual_context_revision": 0,
        "manual_context_updated_at": None,
    }
    assert {
        "ix_matters_legal_area_key",
        "ix_matters_legal_product_key",
        "ix_matters_legal_object_name",
        "ix_matters_workspace_legal_route",
        "ix_matters_workspace_health_subtheme_custom",
    }.issubset(matter_indexes)
    assert {
        "ix_klm_thread_workspace_queue_activity",
        "uq_klm_thread_workspace_identity_matter",
        "uq_klm_thread_workspace_identity_unscoped",
        "ix_klm_omnichannel_threads_assigned_team_id",
        "ix_klm_omnichannel_threads_owning_agent_key",
    }.issubset(thread_indexes)
    assert {
        "ix_scheduled_messages_processing_lease",
        "ux_scheduled_messages_idempotency_key",
    }.issubset(scheduled_indexes)
    assert {
        "ux_subscriptions_stripe_subscription_id_not_null",
        "ux_subscriptions_stripe_customer_id_not_null",
    }.issubset(subscription_indexes)
    assert "ix_whatsapp_messages_inbound_lease" in whatsapp_indexes
    assert "ux_whatsapp_messages_outbound_idempotency_key" in whatsapp_indexes
    assert {
        "uq_oss_chatwoot_active_account_binding",
        "uq_oss_bottlecrm_active_organization_binding",
        "uq_oss_rocketchat_active_server_binding",
        "uq_oss_chattermate_active_organization_binding",
    }.issubset(oss_binding_indexes)


def test_local_schema_backfills_and_enforces_approval_intents():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "approval_queue_items",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("matter_id", String(36), nullable=False),
        Column("approval_type", String(100), nullable=False),
        Column("status", String(40), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO approval_queue_items "
                "(id, matter_id, approval_type, status) VALUES "
                "('approval-1', 'matter-1', 'proposal', 'pending'), "
                "('approval-2', 'matter-1', 'proposal', 'pending')"
            )
        )
        ensure_local_sqlite_schema(connection)
        ensure_local_sqlite_schema(connection)

        intents = (
            connection.execute(text("SELECT intent_key FROM approval_queue_items ORDER BY id"))
            .scalars()
            .all()
        )
        assert intents == ["legacy:approval-1", "legacy:approval-2"]

        with pytest.raises(DBAPIError, match="intent_key is required"):
            connection.execute(
                text(
                    "INSERT INTO approval_queue_items "
                    "(id, matter_id, approval_type, status, intent_key) "
                    "VALUES ('approval-3', 'matter-1', 'proposal', 'pending', '')"
                )
            )

    indexes = {index["name"] for index in inspect(engine).get_indexes("approval_queue_items")}
    assert "uq_approval_pending_intent" in indexes


def test_local_schema_backfills_flow_revisions_for_preexisting_database():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    flows = Table(
        "flows",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("workspace_id", String(36), nullable=True),
        Column("name", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("practice_area", String(100), nullable=False),
        Column("is_template", Boolean, nullable=False),
        Column("is_active", Boolean, nullable=False),
        Column("trigger", String(50), nullable=False),
        Column("trigger_config", JSON, nullable=True),
        Column("nodes", JSON, nullable=True),
        Column("edges", JSON, nullable=True),
        Column("run_count", Integer, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Column("deleted_at", DateTime(timezone=True), nullable=True),
    )
    flow_runs = Table(
        "flow_runs",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("flow_id", String(36), nullable=False),
    )
    metadata.create_all(engine)
    now = datetime.now(UTC)

    with engine.begin() as connection:
        connection.execute(
            flows.insert().values(
                id="flow-1",
                workspace_id="workspace-1",
                name="Flow legado",
                description=None,
                practice_area="universal",
                is_template=False,
                is_active=True,
                trigger="manual",
                trigger_config={"localOnly": True},
                nodes=[{"id": "start", "type": "trigger", "config": {}}],
                edges=[],
                run_count=1,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )
        connection.execute(flow_runs.insert().values(id="run-1", flow_id="flow-1"))

        ensure_local_sqlite_schema(connection)
        ensure_local_sqlite_schema(connection)

        flow_row = (
            connection.execute(
                text("SELECT current_revision, current_revision_id FROM flows WHERE id = 'flow-1'")
            )
            .mappings()
            .one()
        )
        run_row = (
            connection.execute(text("SELECT revision_id FROM flow_runs WHERE id = 'run-1'"))
            .mappings()
            .one()
        )
        revision_row = (
            connection.execute(
                text(
                    "SELECT id, revision_number, snapshot, source "
                    "FROM flow_revisions WHERE flow_id = 'flow-1'"
                )
            )
            .mappings()
            .one()
        )

        assert flow_row["current_revision"] == 1
        assert flow_row["current_revision_id"] == revision_row["id"]
        assert run_row["revision_id"] == revision_row["id"]
        assert revision_row["revision_number"] == 1
        assert revision_row["source"] == "local_schema_backfill"
        snapshot = json.loads(revision_row["snapshot"])
        assert snapshot["name"] == "Flow legado"
        assert snapshot["nodes"][0]["id"] == "start"

        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                text("UPDATE flow_revisions SET source = 'tampered' WHERE id = :revision_id"),
                {"revision_id": revision_row["id"]},
            )

    flow_columns = {column["name"] for column in inspect(engine).get_columns("flows")}
    run_columns = {column["name"] for column in inspect(engine).get_columns("flow_runs")}
    assert {"current_revision", "current_revision_id"}.issubset(flow_columns)
    assert "revision_id" in run_columns


def test_local_schema_protects_support_access_events_from_mutation():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "support_access_events",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("event_type", String(80), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO support_access_events (id, event_type) "
                "VALUES ('event-1', 'support_access_requested')"
            )
        )
        ensure_local_sqlite_schema(connection)
        ensure_local_sqlite_schema(connection)

        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                text(
                    "UPDATE support_access_events SET event_type = 'tampered' WHERE id = 'event-1'"
                )
            )

        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(text("DELETE FROM support_access_events WHERE id = 'event-1'"))


def test_local_schema_backfills_immutable_legal_artifact_and_mutable_state():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("workspaces", metadata, Column("id", String(36), primary_key=True))
    Table(
        "matters",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("workspace_id", String(36), nullable=False),
    )
    Table(
        "approval_queue_items",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("matter_id", String(36), nullable=False),
    )
    artifact_table = Table(
        "klm_legal_artifact_versions",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("workspace_id", String(36), nullable=False),
        Column("matter_id", String(36), nullable=False),
        Column("artifact_key", String(36), nullable=False),
        Column("version", Integer, nullable=False),
        Column("kind", String(40), nullable=False),
        Column("title", String(300), nullable=False),
        Column("status", String(40), nullable=False),
        Column("content", JSON, nullable=False),
        Column("source_snapshot", JSON, nullable=False),
        Column("created_by_user_id", String(36), nullable=False),
        Column("approval_item_id", String(36), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    now = datetime.now(UTC)

    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.execute(text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
        connection.execute(
            text("INSERT INTO matters (id, workspace_id) VALUES ('matter-a', 'workspace-a')")
        )
        connection.execute(
            text(
                "INSERT INTO approval_queue_items (id, matter_id) VALUES ('approval-a', 'matter-a')"
            )
        )
        connection.execute(
            artifact_table.insert(),
            {
                "id": "artifact-a",
                "workspace_id": "workspace-a",
                "matter_id": "matter-a",
                "artifact_key": "artifact-key-a",
                "version": 1,
                "kind": "proposal",
                "title": "Immutable local artifact",
                "status": "review_pending",
                "content": {"body": "canonical"},
                "source_snapshot": {"source": "legacy-local"},
                "created_by_user_id": "user-a",
                "approval_item_id": "approval-a",
                "created_at": now,
                "updated_at": now,
            },
        )

        ensure_local_sqlite_schema(connection)
        connection.execute(
            text(
                "UPDATE klm_legal_artifact_states SET status = 'approved' "
                "WHERE artifact_version_id = 'artifact-a'"
            )
        )
        ensure_local_sqlite_schema(connection)

        artifact = (
            connection.execute(
                text(
                    "SELECT evidence_schema_version, evidence_sha256 "
                    "FROM klm_legal_artifact_versions WHERE id = 'artifact-a'"
                )
            )
            .mappings()
            .one()
        )
        state = (
            connection.execute(
                text(
                    "SELECT status, approval_item_id, approval_matter_id "
                    "FROM klm_legal_artifact_states "
                    "WHERE artifact_version_id = 'artifact-a'"
                )
            )
            .mappings()
            .one()
        )

        from app.models.klm_operations import legal_artifact_evidence_sha256

        assert artifact == {
            "evidence_schema_version": 1,
            "evidence_sha256": legal_artifact_evidence_sha256(
                evidence_schema_version=1,
                workspace_id="workspace-a",
                matter_id="matter-a",
                artifact_key="artifact-key-a",
                version=1,
                kind="proposal",
                title="Immutable local artifact",
                content={"body": "canonical"},
                source_snapshot={"source": "legacy-local"},
                created_by_user_id="user-a",
            ),
        }
        assert state == {
            "status": "approved",
            "approval_item_id": "approval-a",
            "approval_matter_id": "matter-a",
        }
        assert "status" not in {
            column["name"]
            for column in inspect(connection).get_columns("klm_legal_artifact_versions")
        }

        connection.execute(
            text(
                """
                INSERT INTO klm_legal_artifact_versions (
                    id, workspace_id, matter_id, artifact_key, version, kind,
                    title, content, source_snapshot, created_by_user_id,
                    created_at, updated_at, evidence_schema_version,
                    evidence_sha256
                ) VALUES (
                    'artifact-b', 'workspace-a', 'matter-a', 'artifact-key-b',
                    1, 'proposal', 'Current writer artifact', '{}', '{}',
                    'user-a', :created_at, :updated_at, 1, :digest
                )
                """
            ),
            {
                "created_at": now,
                "updated_at": now,
                "digest": "0" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO klm_legal_artifact_states (
                    workspace_id, artifact_version_id, matter_id, status, id,
                    created_at, updated_at
                ) VALUES (
                    'workspace-a', 'artifact-b', 'matter-a', 'draft',
                    'state-b', :created_at, :updated_at
                )
                """
            ),
            {"created_at": now, "updated_at": now},
        )

        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                text(
                    "UPDATE klm_legal_artifact_versions SET title = 'tampered' "
                    "WHERE id = 'artifact-a'"
                )
            )
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                text("DELETE FROM klm_legal_artifact_versions WHERE id = 'artifact-a'")
            )
