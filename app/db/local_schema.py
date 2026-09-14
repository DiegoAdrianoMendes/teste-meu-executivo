"""Compatibilidade incremental para bancos SQLite criados pelo runtime local."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import MetaData, Table, func, inspect, select, text, update
from sqlalchemy.engine import Connection

from app.db.append_only import install_sqlite_immutable_triggers


def _serialized_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def _ensure_membership_permission_columns(connection: Connection) -> None:
    inspector = inspect(connection)
    if "workspace_memberships" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("workspace_memberships")}
    definitions = (
        ("access_package", "VARCHAR(80)"),
        ("permission_grants", "JSON NOT NULL DEFAULT '[]'"),
        ("permission_denials", "JSON NOT NULL DEFAULT '[]'"),
    )
    for column_name, definition in definitions:
        if column_name in existing:
            continue
        connection.execute(
            text(f'ALTER TABLE workspace_memberships ADD COLUMN "{column_name}" {definition}')
        )
        existing.add(column_name)

    if "role" in existing:
        connection.execute(
            text(
                "UPDATE workspace_memberships SET access_package = CASE "
                "WHEN role = 'workspace_owner' THEN 'office_owner' "
                "WHEN role = 'workspace_admin' THEN 'office_admin' "
                "ELSE access_package END WHERE access_package IS NULL"
            )
        )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_workspace_memberships_access_package "
            "ON workspace_memberships (workspace_id, access_package)"
        )
    )


def _ensure_runtime_hardening_columns(connection: Connection) -> None:
    """Aplica adições compatíveis com ``ALTER TABLE`` no banco local existente."""
    table_definitions: dict[str, tuple[tuple[str, str], ...]] = {
        "workspaces": (
            ("owner_whatsapp_phone", "VARCHAR(32)"),
        ),
        "users": (
            ("email_verified_at", "DATETIME"),
            ("auth_epoch", "INTEGER NOT NULL DEFAULT 0"),
            ("platform_role_package", "VARCHAR(80)"),
            ("platform_access_active", "BOOLEAN NOT NULL DEFAULT 0"),
            ("platform_permission_grants", "JSON NOT NULL DEFAULT '[]'"),
            ("platform_permission_denials", "JSON NOT NULL DEFAULT '[]'"),
        ),
        "password_reset_tokens": (
            ("code_hash", "VARCHAR(64)"),
            ("last_delivery_id", "CHAR(36)"),
            ("revoked_at", "DATETIME"),
        ),
        "mfa_credentials": (
            ("totp_secret_encrypted", "TEXT"),
            ("last_totp_counter", "INTEGER"),
        ),
        "subscriptions": (
            ("last_stripe_event_id", "VARCHAR(255)"),
            ("last_stripe_event_created", "BIGINT"),
            ("cancel_at_period_end", "BOOLEAN NOT NULL DEFAULT false"),
        ),
        "blog_posts": (
            ("revision", "INTEGER NOT NULL DEFAULT 1"),
        ),
        "scheduled_messages": (
            ("idempotency_key", "VARCHAR(100)"),
            ("lease_token", "VARCHAR(64)"),
            ("lease_expires_at", "DATETIME"),
            ("delivery_attempted_at", "DATETIME"),
            ("delivery_attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("provider_supports_idempotency", "BOOLEAN NOT NULL DEFAULT 0"),
            ("provider_message_id", "VARCHAR(255)"),
            ("delivery_uncertain_at", "DATETIME"),
        ),
        "whatsapp_messages": (
            ("message_metadata", "JSON NOT NULL DEFAULT '{}'"),
            ("inbound_pipeline", "JSON NOT NULL DEFAULT '{}'"),
            ("inbound_lease_token", "VARCHAR(64)"),
            ("inbound_lease_expires_at", "DATETIME"),
            ("inbound_lease_generation", "BIGINT NOT NULL DEFAULT 0"),
            ("outbound_idempotency_key", "VARCHAR(64)"),
            ("outbound_payload_digest", "VARCHAR(64)"),
            ("outbound_request_started_at", "DATETIME"),
            ("delivery_uncertain_at", "DATETIME"),
            ("outbound_error", "JSON"),
        ),
        "whatsapp_connections": (
            ("transport_mode", "VARCHAR(40) NOT NULL DEFAULT 'official_api'"),
            ("app_secret_encrypted", "TEXT"),
            ("webhook_verify_token_encrypted", "TEXT"),
        ),
        "approval_queue_items": (("intent_key", "VARCHAR(255)"),),
        "revenue_projections": (
            ("projection_premises", "JSON NOT NULL DEFAULT '{}'"),
            ("projection_version", "INTEGER NOT NULL DEFAULT 0"),
        ),
        "matters": (
            ("tags", "JSON NOT NULL DEFAULT '[]'"),
            ("legal_area_key", "VARCHAR(80)"),
            ("legal_product_key", "VARCHAR(100)"),
            ("legal_product_custom", "VARCHAR(180)"),
            ("legal_object_name", "VARCHAR(300)"),
            ("counterparty_name", "VARCHAR(240)"),
            ("legal_issue_summary", "TEXT"),
            ("legal_context", "JSON NOT NULL DEFAULT '{}'"),
            ("health_subtheme_custom", "VARCHAR(160)"),
            ("manual_context_revision", "INTEGER NOT NULL DEFAULT 0"),
            ("manual_context_updated_at", "DATETIME"),
        ),
        "knowledge_chunks": (("embedding_data", "JSON NOT NULL DEFAULT '[]'"),),
        "klm_omnichannel_threads": (
            ("matter_id", "CHAR(36)"),
            ("identity_id", "CHAR(36)"),
            ("assigned_team_id", "CHAR(36)"),
            ("owning_agent_key", "VARCHAR(100)"),
            ("owning_area_key", "VARCHAR(80)"),
            ("owner_since", "DATETIME"),
            ("owner_generation", "INTEGER NOT NULL DEFAULT 0"),
            ("queue", "VARCHAR(60) NOT NULL DEFAULT 'new'"),
            ("priority", "INTEGER NOT NULL DEFAULT 0"),
            ("is_favorite", "BOOLEAN NOT NULL DEFAULT 0"),
            ("preferred_channel", "VARCHAR(40)"),
            ("version", "INTEGER NOT NULL DEFAULT 1"),
            ("last_inbound_at", "DATETIME"),
            ("last_outbound_at", "DATETIME"),
            ("first_response_due_at", "DATETIME"),
            ("resolution_due_at", "DATETIME"),
            ("closed_at", "DATETIME"),
            ("closed_reason", "VARCHAR(180)"),
            ("policy_state", "JSON NOT NULL DEFAULT '{}'"),
            ("cost_state", "JSON NOT NULL DEFAULT '{}'"),
        ),
        "klm_omnichannel_messages": (
            ("endpoint_id", "CHAR(36)"),
            ("binding_id", "CHAR(36)"),
            ("connection_id", "CHAR(36)"),
            ("audience", "VARCHAR(40) NOT NULL DEFAULT 'client_private'"),
            ("content_type", "VARCHAR(40) NOT NULL DEFAULT 'text'"),
            ("language", "VARCHAR(16)"),
            ("provider_status", "VARCHAR(80)"),
            ("provider", "VARCHAR(40)"),
            ("transport", "VARCHAR(60) NOT NULL DEFAULT 'local'"),
            ("cost_amount", "NUMERIC(15, 6)"),
            ("cost_currency", "VARCHAR(3)"),
            ("window_expires_at", "DATETIME"),
            ("template_id", "VARCHAR(180)"),
            ("content_lifecycle_state", "VARCHAR(32) NOT NULL DEFAULT 'original'"),
            ("evidence_event_count", "INTEGER NOT NULL DEFAULT 0"),
            ("evidence_revision_count", "INTEGER NOT NULL DEFAULT 0"),
            ("evidence_chain_sha256", "VARCHAR(64)"),
            ("content_edited_at", "DATETIME"),
            ("content_deleted_at", "DATETIME"),
        ),
        "klm_omnichannel_channel_events": (("connection_id", "CHAR(36)"),),
        "klm_omnichannel_action_intents": (
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("max_attempts", "INTEGER NOT NULL DEFAULT 5"),
            ("next_attempt_at", "DATETIME"),
            ("lease_owner", "VARCHAR(180)"),
            ("lease_expires_at", "DATETIME"),
            ("lease_generation", "INTEGER NOT NULL DEFAULT 0"),
            ("delivery_attempted_at", "DATETIME"),
            ("provider_supports_idempotency", "BOOLEAN NOT NULL DEFAULT 0"),
            ("provider_action_id", "VARCHAR(255)"),
            ("delivery_uncertain_at", "DATETIME"),
            ("completed_at", "DATETIME"),
            ("last_error_code", "VARCHAR(120)"),
            ("last_error_message", "TEXT"),
        ),
        "klm_lead_capture_receipts": (("connection_id", "CHAR(36)"),),
        "klm_omnichannel_scheduled_messages": (
            ("connection_id", "CHAR(36)"),
            ("canonical_message_id", "CHAR(36)"),
            ("created_by_user_id", "CHAR(36)"),
            ("author_type", "VARCHAR(24) NOT NULL DEFAULT 'system'"),
            ("author_label", "VARCHAR(180) NOT NULL DEFAULT 'Agendamento KLM'"),
            ("message_payload", "JSON NOT NULL DEFAULT '{}'"),
            ("lease_generation", "INTEGER NOT NULL DEFAULT 0"),
            ("delivery_attempted_at", "DATETIME"),
            ("provider_supports_idempotency", "BOOLEAN NOT NULL DEFAULT 0"),
            ("provider_message_id", "VARCHAR(255)"),
            ("delivery_uncertain_at", "DATETIME"),
            ("completed_at", "DATETIME"),
            ("last_error_code", "VARCHAR(120)"),
            ("last_error_message", "TEXT"),
        ),
        "klm_omnichannel_call_sessions": (("connection_id", "CHAR(36)"),),
        "tasks": (
            ("workspace_id", "CHAR(36)"),
            ("context_type", "VARCHAR(24) NOT NULL DEFAULT 'matter'"),
            ("origin", "VARCHAR(32) NOT NULL DEFAULT 'legacy'"),
            ("client_id", "CHAR(36)"),
            ("contract_id", "CHAR(36)"),
            ("origin_resource_id", "CHAR(36)"),
            ("origin_label", "VARCHAR(240)"),
            ("idempotency_key", "VARCHAR(180)"),
            ("request_sha256", "VARCHAR(64)"),
        ),
        "whatsapp_campaigns": (
            ("follow_up_flow_id", "CHAR(36)"),
            ("flow_start_mode", "VARCHAR(24) NOT NULL DEFAULT 'manual'"),
            ("pause_reason", "VARCHAR(500)"),
            ("paused_by_policy", "BOOLEAN NOT NULL DEFAULT 0"),
        ),
        "whatsapp_campaign_recipients": (
            ("flow_run_id", "CHAR(36)"),
            ("flow_trigger_event", "VARCHAR(24)"),
            ("flow_started_at", "DATETIME"),
        ),
        "finance_entries": (
            ("category", "VARCHAR(80)"),
            ("cost_center", "VARCHAR(80)"),
            ("reimbursable", "BOOLEAN NOT NULL DEFAULT 0"),
            ("reimbursement_entry_id", "CHAR(36)"),
            ("receipt_document_ref", "CHAR(36)"),
            ("recurrence_id", "CHAR(36)"),
            ("approval_status", "VARCHAR(20) NOT NULL DEFAULT 'not_required'"),
            ("approved_by", "CHAR(36)"),
            ("approved_at", "DATETIME"),
        ),
        # AgentRun V2 is additive in PostgreSQL migrations, but local SQLite
        # databases created before it need the same runtime columns before an
        # ORM read can safely select the V2 model.
        "agent_runs": (
            ("context_type", "VARCHAR(40)"),
            ("context_id", "CHAR(36)"),
            ("deployment_id", "CHAR(36)"),
            ("triggered_by_user_id", "CHAR(36)"),
        ),
        # Deployments introduced by V3 can predate their typed role and
        # binding columns in a persistent local SQLite file. Their defaults
        # preserve the backwards-compatible config-only deployment contract.
        "agent_deployments": (
            ("role_package", "VARCHAR(80) NOT NULL DEFAULT 'legal_operations'"),
            ("tool_bindings", "JSON NOT NULL DEFAULT '[]'"),
            ("knowledge_bindings", "JSON NOT NULL DEFAULT '[]'"),
            ("autonomy_policy", "JSON NOT NULL DEFAULT '{}'"),
            ("human_routing_policy", "JSON NOT NULL DEFAULT '{}'"),
            ("budget_policy", "JSON NOT NULL DEFAULT '{}'"),
        ),
        # ``Base.metadata.create_all`` does not evolve an existing local
        # SQLite table.  Keep the disposable/local QA runtime aligned with
        # the effective-dated tax-profile model used by finance exports.
        "finance_tax_profiles": (
            ("effective_until", "DATE"),
        ),
    }
    table_names = set(inspect(connection).get_table_names())
    for table_name, definitions in table_definitions.items():
        if table_name not in table_names:
            continue
        existing = {column["name"] for column in inspect(connection).get_columns(table_name)}
        for column_name, definition in definitions:
            if column_name in existing:
                continue
            connection.execute(
                text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {definition}')
            )
            existing.add(column_name)

    if "scheduled_messages" in table_names:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_scheduled_messages_processing_lease "
                "ON scheduled_messages (status, lease_expires_at)"
            )
        )
    if "users" in table_names:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_users_platform_access_package "
                "ON users (platform_access_active, platform_role_package)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_scheduled_messages_idempotency_key "
                "ON scheduled_messages (idempotency_key)"
            )
        )
    if "subscriptions" in table_names:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ux_subscriptions_stripe_subscription_id_not_null "
                "ON subscriptions (stripe_subscription_id) "
                "WHERE stripe_subscription_id IS NOT NULL"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ux_subscriptions_stripe_customer_id_not_null "
                "ON subscriptions (stripe_customer_id) "
                "WHERE stripe_customer_id IS NOT NULL"
            )
        )
    if "finance_tax_profiles" in table_names:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_finance_tax_profiles_workspace_effective "
                "ON finance_tax_profiles (workspace_id, effective_from)"
            )
        )
    if "agent_runs" in table_names:
        for index_name, column_name in (
            ("ix_agent_runs_context_type", "context_type"),
            ("ix_agent_runs_context_id", "context_id"),
            ("ix_agent_runs_deployment_id", "deployment_id"),
            ("ix_agent_runs_triggered_by_user_id", "triggered_by_user_id"),
        ):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {index_name} "
                    f"ON agent_runs ({column_name})"
                )
            )
    if "agent_deployments" in table_names:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_agent_deployments_role_package "
                "ON agent_deployments (workspace_id, role_package)"
            )
        )
    if "matters" in table_names:
        matter_columns = {
            column["name"] for column in inspect(connection).get_columns("matters")
        }
        if {"practice_area", "legal_area_key"}.issubset(matter_columns):
            connection.execute(
                text(
                    "UPDATE matters SET legal_area_key = CAST(practice_area AS TEXT) "
                    "WHERE legal_area_key IS NULL"
                )
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_matters_legal_area_key "
                "ON matters (legal_area_key)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_matters_legal_product_key "
                "ON matters (legal_product_key)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_matters_legal_object_name "
                "ON matters (legal_object_name)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_matters_workspace_legal_route "
                "ON matters (workspace_id, legal_area_key, legal_product_key)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_matters_workspace_health_subtheme_custom "
                "ON matters (workspace_id, health_subtheme_custom)"
            )
        )
    if "whatsapp_messages" in table_names:
        whatsapp_columns = {
            column["name"] for column in inspect(connection).get_columns("whatsapp_messages")
        }
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_inbound_lease "
                "ON whatsapp_messages (inbound_lease_expires_at)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ux_whatsapp_messages_outbound_idempotency_key "
                "ON whatsapp_messages (outbound_idempotency_key)"
            )
        )
        if "status" in whatsapp_columns:
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_delivery_reconciliation "
                    "ON whatsapp_messages (status, delivery_uncertain_at)"
                )
            )

    if "whatsapp_connections" in table_names:
        connection_columns = {
            column["name"] for column in inspect(connection).get_columns("whatsapp_connections")
        }
        if {"connection_type", "transport_mode"}.issubset(connection_columns):
            connection.execute(
                text(
                    "UPDATE whatsapp_connections SET transport_mode = 'qr_gateway' "
                    "WHERE connection_type = 'QR_CODE' AND transport_mode = 'official_api'"
                )
            )

    if "klm_omnichannel_threads" in table_names:
        connection.execute(text("DROP INDEX IF EXISTS uq_klm_thread_workspace_identity"))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_klm_thread_workspace_queue_activity "
                "ON klm_omnichannel_threads "
                "(workspace_id, queue, status, last_message_at)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_klm_thread_workspace_identity_matter "
                "ON klm_omnichannel_threads (workspace_id, identity_id, matter_id) "
                "WHERE identity_id IS NOT NULL AND matter_id IS NOT NULL"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_klm_thread_workspace_identity_unscoped "
                "ON klm_omnichannel_threads (workspace_id, identity_id) "
                "WHERE identity_id IS NOT NULL AND matter_id IS NULL"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_klm_omnichannel_threads_assigned_team_id "
                "ON klm_omnichannel_threads (assigned_team_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_klm_omnichannel_threads_owning_agent_key "
                "ON klm_omnichannel_threads (owning_agent_key)"
            )
        )

    if "klm_omnichannel_messages" in table_names:
        for column_name in ("endpoint_id", "binding_id", "connection_id"):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS "
                    f"ix_klm_omnichannel_messages_{column_name} "
                    f"ON klm_omnichannel_messages ({column_name})"
                )
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_klm_omnichannel_messages_content_lifecycle_state "
                "ON klm_omnichannel_messages (content_lifecycle_state)"
            )
        )

    additive_indexes = (
        (
            "klm_omnichannel_channel_events",
            "ix_klm_omnichannel_channel_events_connection_id",
            "connection_id",
        ),
        (
            "klm_lead_capture_receipts",
            "ix_klm_lead_capture_receipts_connection_id",
            "connection_id",
        ),
        (
            "klm_omnichannel_call_sessions",
            "ix_klm_omnichannel_call_sessions_connection_id",
            "connection_id",
        ),
        (
            "klm_omnichannel_scheduled_messages",
            "ix_klm_omnichannel_scheduled_messages_connection_id",
            "connection_id",
        ),
        (
            "klm_omnichannel_scheduled_messages",
            "ix_klm_omnichannel_scheduled_messages_canonical_message_id",
            "canonical_message_id",
        ),
        (
            "klm_omnichannel_scheduled_messages",
            "ix_klm_omnichannel_scheduled_messages_created_by_user_id",
            "created_by_user_id",
        ),
        (
            "whatsapp_campaigns",
            "ix_whatsapp_campaigns_follow_up_flow_id",
            "follow_up_flow_id",
        ),
        (
            "whatsapp_campaign_recipients",
            "ix_whatsapp_campaign_recipients_flow_run_id",
            "flow_run_id",
        ),
    )
    for table_name, index_name, column_name in additive_indexes:
        if table_name not in table_names:
            continue
        columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
        if column_name in columns:
            connection.execute(
                text(
                    f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table_name}" ("{column_name}")'
                )
            )

    if "tasks" in table_names:
        task_columns = {column["name"] for column in inspect(connection).get_columns("tasks")}
        if {"workspace_id", "matter_id"}.issubset(task_columns) and "matters" in table_names:
            connection.execute(
                text(
                    "UPDATE tasks SET workspace_id = "
                    "(SELECT matters.workspace_id FROM matters WHERE matters.id = tasks.matter_id) "
                    "WHERE workspace_id IS NULL"
                )
            )
        if {"matter_id", "idempotency_key"}.issubset(task_columns):
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_task_matter_idempotency "
                    "ON tasks (matter_id, idempotency_key) "
                    "WHERE idempotency_key IS NOT NULL"
                )
            )
        if {"workspace_id", "idempotency_key"}.issubset(task_columns):
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_workspace_idempotency "
                    "ON tasks (workspace_id, idempotency_key) "
                    "WHERE idempotency_key IS NOT NULL"
                )
            )

    if "klm_omnichannel_action_intents" in table_names:
        intent_columns = {
            column["name"]
            for column in inspect(connection).get_columns("klm_omnichannel_action_intents")
        }
        required = {"workspace_id", "state", "scheduled_at", "next_attempt_at"}
        if required.issubset(intent_columns):
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_klm_action_intent_workspace_delivery_due "
                    "ON klm_omnichannel_action_intents "
                    "(workspace_id, state, scheduled_at, next_attempt_at)"
                )
            )

    if "approval_queue_items" in table_names:
        approval_columns = {
            column["name"] for column in inspect(connection).get_columns("approval_queue_items")
        }
        required = {"id", "matter_id", "approval_type", "intent_key", "status"}
        if required.issubset(approval_columns):
            connection.execute(
                text(
                    "UPDATE approval_queue_items "
                    "SET intent_key = 'legacy:' || CAST(id AS TEXT) "
                    "WHERE intent_key IS NULL OR TRIM(intent_key) = ''"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_pending_intent "
                    "ON approval_queue_items (matter_id, approval_type, intent_key) "
                    "WHERE status = 'pending'"
                )
            )
            for operation in ("INSERT", "UPDATE OF intent_key"):
                trigger_name = (
                    "trg_approval_intent_required_insert"
                    if operation == "INSERT"
                    else "trg_approval_intent_required_update"
                )
                connection.execute(
                    text(
                        f"""
                        CREATE TRIGGER IF NOT EXISTS {trigger_name}
                        BEFORE {operation} ON approval_queue_items
                        WHEN NEW.intent_key IS NULL OR TRIM(NEW.intent_key) = ''
                        BEGIN
                            SELECT RAISE(ABORT, 'approval intent_key is required');
                        END
                        """
                    )
                )

    if "oss_external_entity_links" in table_names:
        binding_indexes = (
            (
                "uq_oss_chatwoot_active_account_binding",
                "provider = 'chatwoot' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'account' AND sync_state = 'synced'",
            ),
            (
                "uq_oss_bottlecrm_active_organization_binding",
                "provider = 'bottlecrm' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'organization' AND sync_state = 'synced'",
            ),
            (
                "uq_oss_rocketchat_active_server_binding",
                "provider = 'rocketchat' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'account' AND sync_state = 'synced'",
            ),
            (
                "uq_oss_chattermate_active_organization_binding",
                "provider = 'chattermate' AND klm_entity_type = 'workspace' "
                "AND external_entity_type = 'organization' AND sync_state = 'synced'",
            ),
        )
        for index_name, predicate in binding_indexes:
            connection.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
                    "ON oss_external_entity_links (provider, external_account_id) "
                    f"WHERE {predicate}"
                )
            )


def _flow_revision_snapshot(flow: dict[str, Any], revision_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "revision_id": revision_id,
        "revision": 1,
        "version": 1,
        "flow_id": str(flow["id"]),
        "workspace_id": str(flow["workspace_id"]) if flow["workspace_id"] else None,
        "name": flow["name"],
        "description": flow["description"],
        "practice_area": flow["practice_area"],
        "is_template": bool(flow["is_template"]),
        "is_active": bool(flow["is_active"]),
        "trigger": flow["trigger"],
        "trigger_config": flow["trigger_config"],
        "nodes": flow["nodes"],
        "edges": flow["edges"],
        "run_count": flow["run_count"] or 0,
        "created_at": _serialized_datetime(flow["created_at"]),
        "updated_at": _serialized_datetime(flow["updated_at"]),
        "deleted_at": _serialized_datetime(flow["deleted_at"]),
    }


def _ensure_entitlement_enum_constraints(connection: Connection) -> None:
    """Atualiza CHECKs fechados que o ``create_all`` não consegue evoluir."""

    from app.models.entitlement import EntitlementKey

    table_names = set(inspect(connection).get_table_names())
    allowed_values = tuple(item.value for item in EntitlementKey)
    expression = "entitlement IN (" + ", ".join(
        f"'{value}'" for value in allowed_values
    ) + ")"
    constraint_pattern = re.compile(
        r"CONSTRAINT\s+entitlement_key_enum\s+CHECK\s*"
        r"\(\s*entitlement\s+IN\s*\([^)]*\)\s*\)",
        flags=re.IGNORECASE,
    )
    updated_schema = False

    for table_name in (
        "plan_entitlements",
        "workspace_monthly_entitlement_usage",
        "entitlement_usage_reservations",
        "workspace_entitlement_overrides",
    ):
        if table_name not in table_names:
            continue
        checks = inspect(connection).get_check_constraints(table_name)
        enum_check = next(
            (
                check
                for check in checks
                if check.get("name") == "entitlement_key_enum"
                or "entitlement IN" in str(check.get("sqltext") or "")
            ),
            None,
        )
        if enum_check is None:
            continue
        sqltext = str(enum_check.get("sqltext") or "")
        if all(f"'{value}'" in sqltext for value in allowed_values):
            continue
        definition = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = :table_name"
            ),
            {"table_name": table_name},
        ).scalar_one_or_none()
        if not definition or not constraint_pattern.search(str(definition)):
            raise RuntimeError(
                "Cannot safely upgrade local entitlement constraint: " + table_name
            )
        replacement = f"CONSTRAINT entitlement_key_enum CHECK ({expression})"
        updated_definition = constraint_pattern.sub(replacement, str(definition), count=1)
        try:
            connection.exec_driver_sql("PRAGMA writable_schema=ON")
            connection.execute(
                text(
                    "UPDATE sqlite_master SET sql = :definition "
                    "WHERE type = 'table' AND name = :table_name"
                ),
                {"definition": updated_definition, "table_name": table_name},
            )
        finally:
            connection.exec_driver_sql("PRAGMA writable_schema=OFF")
        updated_schema = True

    if updated_schema:
        schema_version = int(
            connection.exec_driver_sql("PRAGMA schema_version").scalar_one()
        )
        connection.exec_driver_sql(f"PRAGMA schema_version={schema_version + 1}")
        if connection.exec_driver_sql("PRAGMA quick_check").scalar_one() != "ok":
            raise RuntimeError("Local SQLite schema validation failed after entitlement upgrade.")


def _ensure_flow_revision_guards(connection: Connection) -> None:
    statements = (
        """
        CREATE TRIGGER IF NOT EXISTS trg_flow_revisions_no_update
        BEFORE UPDATE ON flow_revisions
        BEGIN
            SELECT RAISE(ABORT, 'flow_revisions are immutable');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_flow_revisions_no_delete
        BEFORE DELETE ON flow_revisions
        BEGIN
            SELECT RAISE(ABORT, 'flow_revisions are immutable');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_flow_runs_revision_required_insert
        BEFORE INSERT ON flow_runs
        WHEN NEW.revision_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM flow_revisions
            WHERE id = NEW.revision_id AND flow_id = NEW.flow_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'flow_run requires a revision from the same flow');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_flow_runs_revision_required_update
        BEFORE UPDATE OF revision_id, flow_id ON flow_runs
        WHEN NEW.revision_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM flow_revisions
            WHERE id = NEW.revision_id AND flow_id = NEW.flow_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'flow_run requires a revision from the same flow');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_flows_revision_pointer_update
        BEFORE UPDATE OF current_revision, current_revision_id ON flows
        WHEN NEW.current_revision_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM flow_revisions
            WHERE id = NEW.current_revision_id
              AND flow_id = NEW.id
              AND revision_number = NEW.current_revision
        )
        BEGIN
            SELECT RAISE(ABORT, 'flow current revision pointer is invalid');
        END
        """,
    )
    for statement in statements:
        connection.execute(text(statement))


def _ensure_flow_revision_schema(connection: Connection) -> None:
    table_names = set(inspect(connection).get_table_names())
    if "flows" not in table_names:
        return

    from app.models.flow import FlowRevision, flow_revision_snapshot_hash

    FlowRevision.__table__.create(connection, checkfirst=True)

    flow_columns = {column["name"] for column in inspect(connection).get_columns("flows")}
    if "current_revision" not in flow_columns:
        connection.execute(
            text("ALTER TABLE flows ADD COLUMN current_revision INTEGER NOT NULL DEFAULT 0")
        )
    if "current_revision_id" not in flow_columns:
        connection.execute(text("ALTER TABLE flows ADD COLUMN current_revision_id CHAR(36)"))

    if "flow_runs" in table_names:
        run_columns = {column["name"] for column in inspect(connection).get_columns("flow_runs")}
        if "revision_id" not in run_columns:
            connection.execute(text("ALTER TABLE flow_runs ADD COLUMN revision_id CHAR(36)"))

    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_flows_current_revision_id ON flows (current_revision_id)"
        )
    )
    if "flow_runs" in table_names:
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_flow_runs_revision_id ON flow_runs (revision_id)")
        )

    metadata = MetaData()
    flows = Table("flows", metadata, autoload_with=connection)
    revisions = Table("flow_revisions", metadata, autoload_with=connection)
    flow_runs = (
        Table("flow_runs", metadata, autoload_with=connection)
        if "flow_runs" in table_names
        else None
    )

    for flow in connection.execute(select(flows)).mappings():
        current_revision = None
        if flow["current_revision_id"]:
            current_revision = (
                connection.execute(
                    select(revisions).where(
                        revisions.c.id == flow["current_revision_id"],
                        revisions.c.flow_id == flow["id"],
                    )
                )
                .mappings()
                .first()
            )
        if current_revision is None:
            current_revision = (
                connection.execute(
                    select(revisions)
                    .where(revisions.c.flow_id == flow["id"])
                    .order_by(revisions.c.revision_number.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )

        if current_revision is None:
            revision_id = str(uuid.uuid4())
            snapshot = _flow_revision_snapshot(dict(flow), revision_id)
            connection.execute(
                revisions.insert().values(
                    id=revision_id,
                    flow_id=flow["id"],
                    revision_number=1,
                    snapshot_schema_version=1,
                    snapshot=snapshot,
                    snapshot_hash=flow_revision_snapshot_hash(snapshot),
                    source="local_schema_backfill",
                    created_by_user_id=None,
                )
            )
            current_revision = {
                "id": revision_id,
                "revision_number": 1,
                "snapshot": snapshot,
                "snapshot_hash": flow_revision_snapshot_hash(snapshot),
            }

        current_snapshot = current_revision["snapshot"]
        if (
            flow_revision_snapshot_hash(current_snapshot) != current_revision["snapshot_hash"]
            or current_snapshot.get("flow_id") != str(flow["id"])
            or current_snapshot.get("revision_id") != str(current_revision["id"])
            or current_snapshot.get("revision") != current_revision["revision_number"]
        ):
            raise RuntimeError(
                f"Flow revision inválida durante bootstrap local: {current_revision['id']}"
            )
        connection.execute(
            update(flows)
            .where(flows.c.id == flow["id"])
            .values(
                current_revision=current_revision["revision_number"],
                current_revision_id=current_revision["id"],
            )
        )

    if flow_runs is not None:
        connection.execute(
            update(flow_runs)
            .where(flow_runs.c.revision_id.is_(None))
            .values(
                revision_id=select(flows.c.current_revision_id)
                .where(flows.c.id == flow_runs.c.flow_id)
                .scalar_subquery()
            )
        )
        missing_revisions = connection.scalar(
            select(func.count()).select_from(flow_runs).where(flow_runs.c.revision_id.is_(None))
        )
        if missing_revisions:
            raise RuntimeError(
                "Bootstrap local não conseguiu vincular todos os flow_runs a revisões"
            )
        _ensure_flow_revision_guards(connection)


def _ensure_support_access_guards(connection: Connection) -> None:
    if "support_access_events" not in inspect(connection).get_table_names():
        return
    statements = (
        """
        CREATE TRIGGER IF NOT EXISTS trg_support_access_events_no_update
        BEFORE UPDATE ON support_access_events
        BEGIN
            SELECT RAISE(ABORT, 'support_access_events are immutable');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_support_access_events_no_delete
        BEFORE DELETE ON support_access_events
        BEGIN
            SELECT RAISE(ABORT, 'support_access_events are immutable');
        END
        """,
    )
    for statement in statements:
        connection.execute(text(statement))


def _decode_legal_artifact_json(value: Any, field_name: str) -> dict[str, Any]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise RuntimeError(
            f"Cannot upgrade local legal artifact evidence: {field_name} is not an object."
        )
    return decoded


def _ensure_legal_artifact_evidence_projection(connection: Connection) -> None:
    table_name = "klm_legal_artifact_versions"
    table_names = set(inspect(connection).get_table_names())
    if table_name not in table_names:
        return

    columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
    legacy_state_columns = {"status", "approval_item_id", "updated_at"}
    has_legacy_state = legacy_state_columns <= columns

    # An earlier local startup may already have installed this guard.
    connection.exec_driver_sql(
        'DROP TRIGGER IF EXISTS "trg_klm_legal_artifact_versions_immutable_update"'
    )
    if "evidence_schema_version" not in columns:
        connection.execute(
            text(
                "ALTER TABLE klm_legal_artifact_versions ADD COLUMN "
                "evidence_schema_version INTEGER NOT NULL DEFAULT 1"
            )
        )
        columns.add("evidence_schema_version")
    if "evidence_sha256" not in columns:
        connection.execute(
            text("ALTER TABLE klm_legal_artifact_versions ADD COLUMN evidence_sha256 VARCHAR(64)")
        )
        columns.add("evidence_sha256")

    matter_mismatches = int(
        connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM klm_legal_artifact_versions AS artifact
                LEFT JOIN matters AS matter ON matter.id = artifact.matter_id
                WHERE matter.id IS NULL OR matter.workspace_id <> artifact.workspace_id
                """
            )
        ).scalar_one()
    )
    if matter_mismatches:
        raise RuntimeError(
            "Cannot upgrade local legal artifact evidence: "
            f"cross_workspace_or_missing_matters={matter_mismatches}."
        )

    if has_legacy_state:
        approval_mismatches = int(
            connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM klm_legal_artifact_versions AS artifact
                    LEFT JOIN approval_queue_items AS approval
                      ON approval.id = artifact.approval_item_id
                    WHERE artifact.approval_item_id IS NOT NULL
                      AND (approval.id IS NULL OR approval.matter_id <> artifact.matter_id)
                    """
                )
            ).scalar_one()
        )
        if approval_mismatches:
            raise RuntimeError(
                "Cannot upgrade local legal artifact evidence: "
                f"cross_matter_or_missing_approvals={approval_mismatches}."
            )

    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_klm_legal_artifact_version_workspace_id "
            "ON klm_legal_artifact_versions (workspace_id, id)"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_klm_legal_artifact_version_state_parent "
            "ON klm_legal_artifact_versions (workspace_id, id, matter_id)"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_queue_item_id_matter "
            "ON approval_queue_items (id, matter_id)"
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS klm_legal_artifact_states (
                workspace_id CHAR(36) NOT NULL,
                artifact_version_id CHAR(36) NOT NULL,
                matter_id CHAR(36) NOT NULL,
                status VARCHAR(40) NOT NULL DEFAULT 'draft',
                approval_item_id CHAR(36),
                approval_matter_id CHAR(36),
                id CHAR(36) NOT NULL PRIMARY KEY,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_klm_legal_artifact_state_workspace
                    FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
                CONSTRAINT fk_klm_legal_artifact_state_workspace_version
                    FOREIGN KEY (workspace_id, artifact_version_id, matter_id)
                    REFERENCES klm_legal_artifact_versions (workspace_id, id, matter_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_klm_legal_artifact_state_approval_matter
                    FOREIGN KEY (approval_item_id, approval_matter_id)
                    REFERENCES approval_queue_items (id, matter_id) ON DELETE SET NULL,
                CONSTRAINT uq_klm_legal_artifact_state_version
                    UNIQUE (workspace_id, artifact_version_id),
                CONSTRAINT ck_klm_legal_artifact_state_approval_pair CHECK (
                    (approval_item_id IS NULL AND approval_matter_id IS NULL)
                    OR (approval_item_id IS NOT NULL AND approval_matter_id IS NOT NULL)
                ),
                CONSTRAINT ck_klm_legal_artifact_state_approval_matter CHECK (
                    approval_matter_id IS NULL OR approval_matter_id = matter_id
                )
            )
            """
        )
    )
    for index_name, column_name in (
        ("ix_klm_legal_artifact_states_workspace_id", "workspace_id"),
        ("ix_klm_legal_artifact_states_artifact_version_id", "artifact_version_id"),
        ("ix_klm_legal_artifact_states_status", "status"),
        ("ix_klm_legal_artifact_states_approval_item_id", "approval_item_id"),
    ):
        connection.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON klm_legal_artifact_states ({column_name})"
            )
        )

    selected_columns = [
        "id",
        "workspace_id",
        "matter_id",
        "artifact_key",
        "version",
        "kind",
        "title",
        "content",
        "source_snapshot",
        "created_by_user_id",
        "created_at",
        "evidence_schema_version",
        "evidence_sha256",
    ]
    if has_legacy_state:
        selected_columns.extend(("status", "approval_item_id", "updated_at"))
    rows = (
        connection.execute(
            text(
                "SELECT "
                + ", ".join(selected_columns)
                + " FROM klm_legal_artifact_versions ORDER BY id"
            )
        )
        .mappings()
        .all()
    )

    from app.models.klm_operations import legal_artifact_evidence_sha256

    for row in rows:
        schema_version = int(row["evidence_schema_version"])
        if schema_version != 1:
            raise RuntimeError(
                "Cannot upgrade local legal artifact evidence: "
                f"unsupported_schema_version={schema_version}."
            )
        expected_sha256 = legal_artifact_evidence_sha256(
            evidence_schema_version=schema_version,
            workspace_id=row["workspace_id"],
            matter_id=row["matter_id"],
            artifact_key=row["artifact_key"],
            version=row["version"],
            kind=row["kind"],
            title=row["title"],
            content=_decode_legal_artifact_json(row["content"], "content"),
            source_snapshot=_decode_legal_artifact_json(row["source_snapshot"], "source_snapshot"),
            created_by_user_id=row["created_by_user_id"],
        )
        stored_sha256 = row["evidence_sha256"]
        if stored_sha256 and stored_sha256 != expected_sha256:
            raise RuntimeError(
                f"Cannot upgrade local legal artifact evidence: digest_mismatch={row['id']}."
            )
        if not stored_sha256:
            connection.execute(
                text(
                    "UPDATE klm_legal_artifact_versions SET evidence_sha256 = :digest "
                    "WHERE id = :artifact_id"
                ),
                {"digest": expected_sha256, "artifact_id": row["id"]},
            )

        state_exists = connection.execute(
            text(
                "SELECT 1 FROM klm_legal_artifact_states "
                "WHERE workspace_id = :workspace_id "
                "AND artifact_version_id = :artifact_id"
            ),
            {"workspace_id": row["workspace_id"], "artifact_id": row["id"]},
        ).scalar_one_or_none()
        if state_exists is not None:
            continue
        if not has_legacy_state:
            raise RuntimeError(
                "Cannot upgrade local legal artifact evidence: "
                f"missing_state_projection={row['id']}."
            )
        approval_item_id = row["approval_item_id"]
        connection.execute(
            text(
                """
                INSERT INTO klm_legal_artifact_states (
                    workspace_id, artifact_version_id, matter_id, status,
                    approval_item_id, approval_matter_id, id, created_at, updated_at
                ) VALUES (
                    :workspace_id, :artifact_id, :matter_id, :status,
                    :approval_item_id, :approval_matter_id, :state_id,
                    :created_at, :updated_at
                )
                """
            ),
            {
                "workspace_id": row["workspace_id"],
                "artifact_id": row["id"],
                "matter_id": row["matter_id"],
                "status": row["status"],
                "approval_item_id": approval_item_id,
                "approval_matter_id": (row["matter_id"] if approval_item_id is not None else None),
                "state_id": str(uuid.uuid4()),
                "created_at": _serialized_datetime(row["created_at"]),
                "updated_at": _serialized_datetime(row["updated_at"]),
            },
        )

    if has_legacy_state:
        # ``status`` was non-nullable in the original local table and had no
        # server default. Once workflow state moved to the projection table,
        # leaving that column behind made every ORM insert fail before the
        # projection could be created.
        connection.execute(text("DROP INDEX IF EXISTS ix_klm_legal_artifact_versions_status"))
        connection.execute(text("ALTER TABLE klm_legal_artifact_versions DROP COLUMN status"))


def _ensure_append_only_evidence_guards(connection: Connection) -> None:
    table_names = set(inspect(connection).get_table_names())
    for table_name in (
        "klm_audit_events",
        "klm_legal_artifact_versions",
        "klm_omnichannel_message_cost_ledger",
        "klm_omnichannel_message_delivery_events",
        "klm_omnichannel_public_ingress_receipts",
        "klm_omnichannel_call_events",
        "klm_omnichannel_call_cost_ledger",
        "klm_omnichannel_scheduled_message_events",
        "klm_omnichannel_provider_webhook_envelopes",
        "klm_omnichannel_message_evidence_events",
        "oss_native_audit_events",
        "oss_function_unit_events",
    ):
        if table_name in table_names:
            install_sqlite_immutable_triggers(connection, table_name)


def ensure_local_sqlite_schema(connection: Connection) -> None:
    """Evolui de forma idempotente o SQLite local sem descartar dados existentes."""
    if connection.dialect.name != "sqlite":
        return
    _ensure_membership_permission_columns(connection)
    _ensure_runtime_hardening_columns(connection)
    _ensure_entitlement_enum_constraints(connection)
    _ensure_flow_revision_schema(connection)
    _ensure_support_access_guards(connection)
    _ensure_legal_artifact_evidence_projection(connection)
    _ensure_append_only_evidence_guards(connection)
