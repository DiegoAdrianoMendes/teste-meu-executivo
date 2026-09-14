from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.db.base import Base
from app.db.rls import (
    CASE_360_DIRECT_TENANT_TABLES,
    CASE_STRATEGY_DIRECT_TENANT_TABLES,
    COMMERCIAL_CONTROL_TABLES,
    COMMERCIAL_PLATFORM_INSERT_POLICY_NAME,
    COMMERCIAL_PLATFORM_ONLY_TABLES,
    COMMERCIAL_PLATFORM_READ_POLICY_NAME,
    COMMERCIAL_PLATFORM_UPDATE_POLICY_NAME,
    COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY,
    COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME,
    COMMERCIAL_PORTABILITY_GUARD_TRIGGERS,
    COMMERCIAL_RLS_POLICY_SPECS,
    COMMERCIAL_TENANT_READ_POLICY_NAME,
    COMMERCIAL_TENANT_READ_TABLES,
    CONTRACT_BILLING_DIRECT_TENANT_TABLES,
    CONVERSATION_COLLABORATION_DIRECT_TENANT_TABLES,
    DOMAIN_DIRECT_TENANT_TABLES,
    EXPECTED_POLICY_CATALOG_PREDICATE,
    FEE_ARRANGEMENT_DIRECT_TENANT_TABLES,
    FINANCE_CHARGE_DIRECT_TENANT_TABLES,
    FINANCE_RECURRENCE_DIRECT_TENANT_TABLES,
    FLOW_RUNTIME_DIRECT_TENANT_TABLES,
    IDENTITY_RESOLVER_FUNCTIONS,
    KLM_NATIVE_TENANT_TABLES,
    KLM_TENANT_TABLES,
    LEGACY_RLS_POLICY_NAME,
    LEGACY_RLS_TENANT_TABLES,
    LEGAL_BATCH_DIRECT_TENANT_TABLES,
    LEGAL_CONTROLADORIA_DIRECT_TENANT_TABLES,
    LEGAL_DMS_DIRECT_TENANT_TABLES,
    LEGAL_PRODUCTION_DIRECT_TENANT_TABLES,
    META_GROUP_DIRECT_TENANT_TABLES,
    MIGRATION_DIRECT_TENANT_TABLES,
    NATIVE_GROWTH_DIRECT_TENANT_TABLES,
    OMNICHANNEL_V6_DIRECT_TENANT_TABLES,
    OSS_NATIVE_TENANT_TABLES,
    PARENT_WORKSPACE_POLICIES,
    PLATFORM_TASK_CONTROL_TABLES,
    PLATFORM_TASK_INSERT_POLICY_NAME,
    PLATFORM_TASK_RLS_POLICY_SPECS,
    PLATFORM_TASK_SELECT_POLICY_NAME,
    PLATFORM_TASK_UPDATE_POLICY_NAME,
    PROCESS_MONITORING_DIRECT_TENANT_TABLES,
    PROTOCOL_FOLDER_DIRECT_TENANT_TABLES,
    RLS_DIRECT_TENANT_TABLES,
    RLS_EXEMPT_DIRECT_TENANT_TABLES,
    RLS_PARENT_TENANT_TABLES,
    RLS_POLICY_NAME,
    RLS_POLICY_SPECS,
    RLS_TENANT_TABLES,
    STAFF_AVAILABILITY_DIRECT_TENANT_TABLES,
    SUPPORT_DIRECT_TENANT_TABLES,
    TASK_COLLABORATION_DIRECT_TENANT_TABLES,
    TASK_REMINDER_DIRECT_TENANT_TABLES,
    TENANT_RESOLVER_FUNCTION_SIGNATURES,
    RLSRuntimeConfigurationError,
    _audit_commercial_portability_guard,
    commercial_platform_insert_policy_predicate,
    commercial_platform_mutation_policy_predicate,
    commercial_platform_read_policy_predicate,
    commercial_platform_update_policy_predicate,
    commercial_tenant_read_policy_predicate,
    expected_runtime_table_privileges,
    install_rls_runtime_guard,
    parent_workspace_policy_predicate,
    platform_task_control_policy_predicate,
    policy_expression_matches,
    workspace_policy_predicate,
)
from app.db.tenant import (
    TenantSession,
    WorkspaceContextError,
    get_workspace_context,
    set_workspace_context,
)


@pytest.mark.asyncio
async def test_workspace_context_is_a_safe_sqlite_noop() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    install_rls_runtime_guard(engine)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
    )
    workspace_id = uuid4()

    try:
        async with session_factory() as session:
            await set_workspace_context(session, workspace_id)

            assert get_workspace_context(session) == workspace_id
            assert not session.in_transaction()
            assert await session.scalar(text("SELECT 1")) == 1

            await session.commit()
            assert await session.scalar(text("SELECT 2")) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_context_cannot_switch_tenants_on_sqlite() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
    )

    try:
        async with session_factory() as session:
            await set_workspace_context(session, uuid4())
            with pytest.raises(WorkspaceContextError):
                await set_workspace_context(session, uuid4())
    finally:
        await engine.dispose()


def test_rls_inventory_and_predicate_are_fail_closed() -> None:
    assert len(RLS_TENANT_TABLES) == 256
    assert len(set(RLS_TENANT_TABLES)) == len(RLS_TENANT_TABLES)
    assert {
        "meta_webhook_automation_policies",
        "meta_webhook_operational_states",
        "meta_webhook_receipts",
        "meta_webhook_subscription_jobs",
        "meta_webhook_subscription_states",
    } <= set(RLS_DIRECT_TENANT_TABLES)
    assert set(LEGACY_RLS_TENANT_TABLES).isdisjoint(DOMAIN_DIRECT_TENANT_TABLES)
    assert set(RLS_PARENT_TENANT_TABLES) == set(PARENT_WORKSPACE_POLICIES)
    assert set(RLS_PARENT_TENANT_TABLES).isdisjoint(DOMAIN_DIRECT_TENANT_TABLES)
    assert {
        "clients",
        "booking_links",
        "config_documents",
        "matters",
        "documents",
        "config_document_versions",
        "entitlement_usage_reservations",
        "workspace_entitlement_overrides",
        "executive_profiles",
        "executive_threads",
        "executive_thread_shares",
        "executive_rounds",
        "executive_source_receipts",
        "executive_messages",
        "executive_reports",
        "approval_queue_items",
        "finance_entries",
        "finance_reminders",
        "finance_charges",
        "asaas_webhook_receipts",
        "finance_recurrences",
        "finance_recurrence_runs",
        "fee_arrangements",
        "time_entries",
        "monitored_processes",
        "process_publications",
        "legal_exceptions",
        "migration_jobs",
        "migration_staging_records",
        "migration_divergences",
        "distribution_rules",
        "distribution_decisions",
        "sla_escalation_logs",
        "contract_billing_proposals",
        "bank_transactions",
        "reconciliation_matches",
        "finance_harmonization_runs",
        "finance_harmonization_items",
        "finance_harmonization_quotas",
        "klm_omnichannel_threads",
        "whatsapp_contacts",
        "whatsapp_messages",
        "agent_runs",
        "flow_runs",
        "human_overrides",
        "klm_email_ingest_receipts",
        "klm_legal_artifact_versions",
        "klm_legal_artifact_states",
        "klm_omnichannel_message_evidence_events",
        "klm_process_monitoring_records",
        "public_api_mutation_receipts",
        "public_api_request_logs",
        "scheduled_messages",
        "signup_verifications",
        "storage_objects",
        "subscription_access_states",
        "subscription_access_transitions",
        "token_budgets",
        "matter_treatment_estimates",
        "playbook_scope_bindings",
        "playbook_simulation_runs",
        "playbook_version_governance",
        "workspace_monthly_entitlement_usage",
        "workspace_invitations",
        "workspace_model_credentials",
        "workspace_ai_model_routes",
        "ai_gateway_events",
        "task_events",
        "task_participants",
        "task_review_rounds",
        *COMMERCIAL_CONTROL_TABLES,
        *PLATFORM_TASK_CONTROL_TABLES,
    } <= set(RLS_TENANT_TABLES)
    assert {
        "klm_email_ingest_receipts",
        "klm_legal_artifact_versions",
        "klm_omnichannel_message_evidence_events",
        "klm_process_monitoring_records",
        "public_api_mutation_receipts",
        "public_api_request_logs",
        "playbook_scope_bindings",
        "playbook_simulation_runs",
        "playbook_version_governance",
        "signup_verifications",
        "storage_objects",
        "subscription_access_states",
        "subscription_access_transitions",
        "workspace_monthly_entitlement_usage",
        "workspace_invitations",
        "workspace_model_credentials",
        "workspace_ai_model_routes",
        "ai_gateway_events",
        *TASK_COLLABORATION_DIRECT_TENANT_TABLES,
        *TASK_REMINDER_DIRECT_TENANT_TABLES,
    } <= set(RLS_DIRECT_TENANT_TABLES)
    assert {
        "task_lists",
        "task_list_memberships",
        "task_boards",
        "task_board_columns",
        "task_board_placements",
    } <= set(TASK_COLLABORATION_DIRECT_TENANT_TABLES)
    assert {
        "klm_email_ingest_receipts",
        "klm_legal_artifact_versions",
        "klm_legal_artifact_states",
        "klm_process_monitoring_records",
    } <= set(KLM_NATIVE_TENANT_TABLES)
    assert KLM_TENANT_TABLES == KLM_NATIVE_TENANT_TABLES
    assert {
        "klm_ads_conversion_feedback_records",
        "klm_ads_manager_entities",
        "klm_ads_manager_import_receipts",
        "klm_ads_metric_observations",
        "klm_lead_hydration_events",
        "klm_lead_hydration_jobs",
        "whatsapp_audience_list_members",
        "whatsapp_audience_lists",
    } == set(NATIVE_GROWTH_DIRECT_TENANT_TABLES)
    assert {
        "oss_native_capability_states",
        "oss_native_commands",
        "oss_native_audit_events",
        "oss_native_records",
        "oss_native_outbox_events",
        "oss_function_unit_events",
    } == set(OSS_NATIVE_TENANT_TABLES)
    assert {
        "klm_meta_group_capabilities",
        "klm_meta_groups",
        "klm_meta_group_participants",
        "klm_meta_group_operations",
        "klm_meta_group_lifecycle_events",
    } == set(META_GROUP_DIRECT_TENANT_TABLES)
    assert {
        "klm_automation_trigger_receipts",
        "klm_lead_capture_receipts",
        "klm_omnichannel_action_intent_events",
        "klm_omnichannel_action_intents",
        "klm_omnichannel_audit_findings",
        "klm_omnichannel_automation_rules",
        "klm_omnichannel_automation_run_events",
        "klm_omnichannel_automation_runs",
        "klm_omnichannel_call_cost_ledger",
        "klm_omnichannel_call_events",
        "klm_omnichannel_call_sessions",
        "klm_omnichannel_channel_connections",
        "klm_omnichannel_channel_events",
        "klm_omnichannel_endpoints",
        "klm_omnichannel_identities",
        "klm_omnichannel_identity_mutation_events",
        "klm_omnichannel_identity_reviews",
        "klm_omnichannel_message_cost_ledger",
        "klm_omnichannel_message_delivery_events",
        "klm_omnichannel_message_evidence_events",
        "klm_omnichannel_provider_media_upload_intents",
        "klm_omnichannel_provider_media_upload_reconciliations",
        "klm_omnichannel_provider_media_receipts",
        "klm_omnichannel_public_ingress_receipts",
        "klm_omnichannel_routing_events",
        "klm_omnichannel_scheduled_messages",
        "klm_omnichannel_scheduled_message_events",
        "klm_omnichannel_thread_bindings",
        "klm_omnichannel_webhook_credentials",
        "klm_workspace_team_members",
        "klm_workspace_teams",
    } == set(OMNICHANNEL_V6_DIRECT_TENANT_TABLES)
    assert set(CONVERSATION_COLLABORATION_DIRECT_TENANT_TABLES) == {
        "klm_conversation_groups",
        "klm_conversation_group_participants",
        "klm_conversation_mention_operations",
        "klm_conversation_mentions",
        "klm_conversation_message_references",
    }
    assert set(STAFF_AVAILABILITY_DIRECT_TENANT_TABLES) == {
        "staff_availability_audit_events",
        "staff_availability_change_requests",
        "staff_availability_exceptions",
        "staff_availability_profiles",
        "staff_recurring_availability",
    }
    assert set(SUPPORT_DIRECT_TENANT_TABLES) == {
        "support_notification_outbox",
        "support_ticket_attachments",
        "support_ticket_collaborators",
        "support_ticket_events",
        "support_ticket_messages",
        "support_tickets",
        "support_transcript_emails",
    }
    assert set(MIGRATION_DIRECT_TENANT_TABLES) == {
        "migration_jobs",
        "migration_staging_records",
        "migration_divergences",
    }
    assert set(LEGAL_CONTROLADORIA_DIRECT_TENANT_TABLES) == {
        "distribution_rules",
        "distribution_decisions",
        "sla_escalation_logs",
    }
    assert set(CASE_STRATEGY_DIRECT_TENANT_TABLES) == {
        "case_facts",
        "case_proof_links",
        "case_pleadings",
    }
    assert set(CASE_360_DIRECT_TENANT_TABLES) == {"case_parties"}
    assert set(LEGAL_PRODUCTION_DIRECT_TENANT_TABLES) == {
        "legal_citation_verifications",
        "legal_review_findings",
    }
    assert set(LEGAL_DMS_DIRECT_TENANT_TABLES) == {
        "document_assets",
        "document_asset_references",
        "document_share_links",
        "document_share_downloads",
    }
    assert set(PROTOCOL_FOLDER_DIRECT_TENANT_TABLES) == {"protocol_folders"}
    assert set(LEGAL_BATCH_DIRECT_TENANT_TABLES) == {"legal_batches", "legal_batch_items"}

    predicate = workspace_policy_predicate()
    assert "current_setting('app.current_workspace_id', true)" in predicate
    assert "NULLIF" in predicate
    assert "workspace_id::text" in predicate
    assert policy_expression_matches("clients", EXPECTED_POLICY_CATALOG_PREDICATE)
    assert not policy_expression_matches("clients", f"({predicate}) OR true")

    document_predicate = parent_workspace_policy_predicate("documents")
    assert document_predicate.startswith("EXISTS (SELECT 1 FROM matters")
    assert "matters.id = documents.matter_id" in document_predicate
    assert "matters.workspace_id::text" in document_predicate
    assert policy_expression_matches("documents", document_predicate)

    message_predicate = parent_workspace_policy_predicate("whatsapp_messages")
    assert "whatsapp_contacts.id = whatsapp_messages.contact_id" in message_predicate
    assert (
        "whatsapp_contacts.whatsapp_connection_id = whatsapp_messages.whatsapp_connection_id"
    ) in message_predicate


def test_new_control_plane_predicates_require_attributable_context() -> None:
    platform_tasks = platform_task_control_policy_predicate()
    assert "platform_root" in platform_tasks
    assert "app.current_platform_actor_id" in platform_tasks
    assert "platform_tasks:view" in platform_tasks
    assert "platform_tasks:assign" in platform_tasks
    assert "current_workspace_id" not in platform_tasks

    contract_tenant = commercial_tenant_read_policy_predicate(
        "workspace_commercial_contracts"
    )
    assert "app.current_workspace_id" in contract_tenant
    assert "app.current_platform_actor_id" not in contract_tenant

    contract_read = commercial_platform_read_policy_predicate(
        "workspace_commercial_contracts"
    )
    assert "app.current_workspace_id" not in contract_read
    assert "app.current_platform_actor_id" in contract_read
    assert "commercial_contracts:view" in contract_read
    assert "commercial_adjustments:view" in contract_read
    for workflow_module in ("sales", "customers", "finance"):
        assert f"{workflow_module}:manage" in contract_read

    for table_name in ("commercial_offers", "commercial_offer_versions"):
        offer_read = commercial_platform_read_policy_predicate(table_name)
        assert "app.current_platform_actor_id" in offer_read
        assert "platform_root" in offer_read
        assert "plans:view" in offer_read
        assert "plans:manage" in offer_read
        assert "plans:draft" in offer_read
        assert "plans:preview" in offer_read
        assert "plans:publish" in offer_read
        assert "plans:restore" in offer_read
        assert "commercial_contracts:view" in offer_read
        assert "commercial_contracts:manage" in offer_read
        for workflow_module in ("sales", "customers", "finance"):
            assert f"{workflow_module}:manage" in offer_read
        assert "support:view" not in offer_read

    contract_insert = commercial_platform_insert_policy_predicate(
        "workspace_commercial_contracts"
    )
    contract_update = commercial_platform_update_policy_predicate(
        "workspace_commercial_contracts"
    )
    assert "commercial_contracts:manage" in contract_insert
    assert "commercial_contracts:manage" in contract_update
    assert "commercial_contracts:view" not in contract_insert
    for workflow_module in ("sales", "customers", "finance"):
        assert f"{workflow_module}:manage" in contract_insert
        assert f"{workflow_module}:manage" in contract_update
    assert "app.current_workspace_id" not in contract_insert

    offer_insert = commercial_platform_insert_policy_predicate(
        "commercial_offer_versions"
    )
    assert "platform_root" in offer_insert
    assert "plans:view" not in offer_insert
    assert "plans:publish" not in offer_insert
    for workflow_module in ("sales", "customers", "finance"):
        assert f"{workflow_module}:manage" not in offer_insert

    event_insert = commercial_platform_insert_policy_predicate(
        "commercial_catalog_events"
    )
    assert "current_workspace_id" not in event_insert
    assert "platform_root" in event_insert
    assert "plans:draft" in event_insert
    assert "plans:publish" in event_insert
    assert "plans:restore" in event_insert
    assert "plans:preview" not in event_insert
    assert "plans:view" not in event_insert
    assert "commercial_adjustments:manage" in event_insert
    for workflow_module in ("sales", "customers", "finance"):
        assert f"{workflow_module}:manage" in event_insert

    adjustment_insert = commercial_platform_insert_policy_predicate(
        "commercial_adjustments"
    )
    adjustment_update = commercial_platform_update_policy_predicate(
        "commercial_adjustments"
    )
    bonus_insert = commercial_platform_insert_policy_predicate("entitlement_bonuses")
    bonus_update = commercial_platform_update_policy_predicate("entitlement_bonuses")
    assert "commercial_adjustments:manage" in adjustment_insert
    assert "commercial_adjustments:manage" in adjustment_update
    assert "entitlement_bonuses:manage" in bonus_insert
    assert "entitlement_bonuses:manage" in bonus_update
    assert "commercial_portability:rebind" not in adjustment_insert
    assert "commercial_portability:rebind" in adjustment_update
    assert "commercial_portability:rebind" not in bonus_insert
    assert "commercial_portability:rebind" in bonus_update
    assert "commercial_portability:restore_rebind" not in adjustment_insert
    assert "commercial_portability:restore_rebind" in adjustment_update
    assert "commercial_portability:restore_rebind" not in bonus_insert
    assert "commercial_portability:restore_rebind" not in bonus_update
    for capability in (
        "commercial_contracts:manage",
        "sales:manage",
        "customers:manage",
        "finance:manage",
    ):
        assert capability not in adjustment_insert
        assert capability not in adjustment_update
        assert capability not in bonus_insert
        assert capability not in bonus_update

    event_read = commercial_platform_read_policy_predicate(
        "commercial_catalog_events"
    )
    assert "plans:view" in event_read
    assert "plans:draft" in event_read
    assert "plans:preview" in event_read
    assert "plans:publish" in event_read
    assert "plans:restore" in event_read

    receipt_read = commercial_platform_read_policy_predicate(
        "commercial_command_receipts"
    )
    receipt_insert = commercial_platform_insert_policy_predicate(
        "commercial_command_receipts"
    )
    for action in ("draft", "publish", "restore"):
        assert f"plans:{action}" in receipt_read
        assert f"plans:{action}" in receipt_insert
    assert "plans:view" not in receipt_read
    assert "plans:preview" not in receipt_read

    generic_mutation = commercial_platform_mutation_policy_predicate(
        "workspace_commercial_contracts"
    )
    assert "platform_root" in generic_mutation
    assert "commercial_contracts:manage" not in generic_mutation
    assert (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME
        == "klm_guard_commercial_portability_update"
    )
    assert COMMERCIAL_PORTABILITY_GUARD_TRIGGERS == {
        "commercial_adjustments": "trg_commercial_adjustments_portability_guard",
        "entitlement_bonuses": "trg_entitlement_bonuses_portability_guard",
    }
    for immutable_expression in (
        "NEW.id IS DISTINCT FROM OLD.id",
        "NEW.contract_id IS DISTINCT FROM OLD.contract_id",
        "NEW.type IS DISTINCT FROM OLD.type",
        "NEW.value IS DISTINCT FROM OLD.value",
        "NEW.starts_at IS DISTINCT FROM OLD.starts_at",
        "NEW.ends_at IS DISTINCT FROM OLD.ends_at",
        "NEW.remaining_cycles IS DISTINCT FROM OLD.remaining_cycles",
        "NEW.stack_group IS DISTINCT FROM OLD.stack_group",
        "NEW.stackable IS DISTINCT FROM OLD.stackable",
        "NEW.portability IS DISTINCT FROM OLD.portability",
        "NEW.reason IS DISTINCT FROM OLD.reason",
        "NEW.source IS DISTINCT FROM OLD.source",
        "NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id",
        "NEW.created_at IS DISTINCT FROM OLD.created_at",
        "NEW.workspace_id IS DISTINCT FROM OLD.workspace_id",
        "NEW.entitlement IS DISTINCT FROM OLD.entitlement",
        "NEW.mode IS DISTINCT FROM OLD.mode",
        "NEW.quantity IS DISTINCT FROM OLD.quantity",
        "NEW.renews_each_period IS DISTINCT FROM OLD.renews_each_period",
    ):
        assert immutable_expression in COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    assert "NEW.version <> OLD.version + 1" in (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    )
    assert "NEW.updated_at <= OLD.updated_at" in (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    )
    assert "OLD.portability = 'preserve'" in (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    )
    assert "OLD.portability = 'expire_on_offer_change'" in (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    )
    assert "OLD.portability <> 'expire_on_offer_change'" in (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    )
    assert "OLD.status IN ('scheduled', 'active')" in (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    )
    assert "OLD.status NOT IN ('scheduled', 'active')" in (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    )
    assert "NEW.status = 'revoked'" in COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    assert "'commercial_portability:restore_rebind'" in (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    )
    assert (
        "OLD.portability IN ("
        in COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    )
    for restore_portability in (
        "'preserve'",
        "'expire_on_offer_change'",
        "'manual_review'",
        "'recalculate'",
    ):
        assert restore_portability in COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    assert "restore portability cannot update bonuses" in (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY
    )


def _commercial_portability_trigger_rows() -> list[tuple[object, ...]]:
    return [
        (
            table_name,
            trigger_name,
            COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME,
            "public",
            "O",
            19,
            True,
            "",
            0,
            True,
            (
                f"CREATE TRIGGER {trigger_name} BEFORE UPDATE ON public.{table_name} "
                "FOR EACH ROW EXECUTE FUNCTION "
                f"public.{COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME}()"
            ),
        )
        for table_name, trigger_name in (
            COMMERCIAL_PORTABILITY_GUARD_TRIGGERS.items()
        )
    ]


def _commercial_portability_guard_cursor(
    trigger_rows: list[tuple[object, ...]],
) -> Mock:
    cursor = Mock()
    cursor.fetchone.return_value = (
        COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY,
        False,
        "plpgsql",
        ["search_path=pg_catalog, pg_temp"],
        False,
    )
    cursor.fetchall.return_value = trigger_rows
    return cursor


def test_commercial_portability_guard_accepts_only_exact_public_triggers() -> None:
    cursor = _commercial_portability_guard_cursor(
        _commercial_portability_trigger_rows()
    )

    _audit_commercial_portability_guard(cursor)

    assert cursor.execute.call_count == 2
    trigger_query = str(cursor.execute.call_args_list[1].args[0])
    assert "pg_get_triggerdef(trigger.oid)" in trigger_query
    assert "function_namespace.nspname" in trigger_query
    assert "trigger.tgqual IS NULL" in trigger_query
    assert "trigger.tgenabled::text" in trigger_query


@pytest.mark.parametrize(
    ("drift", "expected_invariants"),
    (
        ("function_schema", ("function_namespace", "exact_function", "definition")),
        ("when_clause", ("no_when_clause", "definition")),
        ("disabled", ("enabled",)),
    ),
)
def test_commercial_portability_guard_rejects_trigger_definition_drift(
    drift: str,
    expected_invariants: tuple[str, ...],
) -> None:
    rows = _commercial_portability_trigger_rows()
    first = list(rows[0])
    if drift == "function_schema":
        first[3] = "shadow"
        first[9] = False
        first[10] = str(first[10]).replace(
            f"public.{COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME}()",
            f"shadow.{COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME}()",
        )
    elif drift == "when_clause":
        first[6] = False
        first[10] = str(first[10]).replace(
            "FOR EACH ROW",
            "FOR EACH ROW WHEN ((NEW.version > 0))",
        )
    else:
        first[4] = "D"
    rows[0] = tuple(first)

    with pytest.raises(
        RLSRuntimeConfigurationError,
        match="trigger is unsafe or drifted",
    ) as raised:
        _audit_commercial_portability_guard(
            _commercial_portability_guard_cursor(rows)
        )
    message = str(raised.value)
    assert "public.commercial_adjustments/" in message
    assert "expected=" in message
    assert "actual=" in message
    for invariant in expected_invariants:
        assert f"{invariant}:" in message
    if "definition" in expected_invariants:
        assert "sha256:" in message
        assert "WHEN ((NEW.version > 0))" not in message


def test_commercial_policy_inventory_splits_tenant_reads_from_platform_writes() -> None:
    assert set(COMMERCIAL_TENANT_READ_TABLES) | set(COMMERCIAL_PLATFORM_ONLY_TABLES) == set(
        COMMERCIAL_CONTROL_TABLES
    )
    assert set(COMMERCIAL_TENANT_READ_TABLES).isdisjoint(COMMERCIAL_PLATFORM_ONLY_TABLES)
    assert set(RLS_POLICY_SPECS) == set(RLS_TENANT_TABLES)

    for table_name in COMMERCIAL_TENANT_READ_TABLES:
        specs = COMMERCIAL_RLS_POLICY_SPECS[table_name]
        expected = [
            (COMMERCIAL_TENANT_READ_POLICY_NAME, "SELECT"),
            (COMMERCIAL_PLATFORM_READ_POLICY_NAME, "SELECT"),
            (COMMERCIAL_PLATFORM_INSERT_POLICY_NAME, "INSERT"),
        ]
        if table_name in {
            "commercial_adjustments",
            "entitlement_bonuses",
            "workspace_commercial_contracts",
            "workspace_contract_lines",
        }:
            expected.append((COMMERCIAL_PLATFORM_UPDATE_POLICY_NAME, "UPDATE"))
        assert [(spec.name, spec.command) for spec in specs] == expected
        tenant, platform_read, platform_insert, *platform_update = specs
        assert tenant.with_check is None
        assert platform_read.with_check is None
        assert platform_insert.using is None
        assert platform_insert.with_check is not None
        if platform_update:
            assert platform_update[0].with_check == platform_update[0].using
        assert all(spec.command != "DELETE" for spec in specs)

    for table_name in COMMERCIAL_PLATFORM_ONLY_TABLES:
        specs = COMMERCIAL_RLS_POLICY_SPECS[table_name]
        assert [(spec.name, spec.command) for spec in specs] == [
            (COMMERCIAL_PLATFORM_READ_POLICY_NAME, "SELECT"),
            (COMMERCIAL_PLATFORM_INSERT_POLICY_NAME, "INSERT"),
        ]
        assert specs[0].with_check is None
        assert specs[1].using is None
        assert specs[1].with_check is not None

    with pytest.raises(KeyError):
        commercial_tenant_read_policy_predicate("commercial_catalog_events")


def test_platform_task_policy_inventory_is_split_by_exact_operation() -> None:
    for table_name in PLATFORM_TASK_CONTROL_TABLES:
        specs = PLATFORM_TASK_RLS_POLICY_SPECS[table_name]
        expected = [
            (PLATFORM_TASK_SELECT_POLICY_NAME, "SELECT"),
            (PLATFORM_TASK_INSERT_POLICY_NAME, "INSERT"),
        ]
        if table_name != "platform_task_events":
            expected.append((PLATFORM_TASK_UPDATE_POLICY_NAME, "UPDATE"))
        assert [(spec.name, spec.command) for spec in specs] == expected
        assert all(spec.command != "DELETE" for spec in specs)
        assert specs[0].with_check is None
        assert specs[1].using is None
        assert specs[1].with_check is not None

    assert "platform_tasks:view" in PLATFORM_TASK_RLS_POLICY_SPECS["platform_tasks"][0].using
    assert "platform_tasks:create" in PLATFORM_TASK_RLS_POLICY_SPECS["platform_tasks"][1].with_check
    task_update = PLATFORM_TASK_RLS_POLICY_SPECS["platform_tasks"][2]
    assert "platform_tasks:view" not in task_update.using
    assert "platform_tasks:edit" in task_update.using
    assert "platform_tasks:review" in task_update.using

    event_specs = PLATFORM_TASK_RLS_POLICY_SPECS["platform_task_events"]
    assert len(event_specs) == 2
    assert "platform_tasks:view" in event_specs[0].using
    assert "platform_tasks:edit" in event_specs[1].with_check
    assert "platform_tasks:view" not in event_specs[1].with_check


def test_runtime_table_privilege_matrix_matches_policy_and_append_only_boundaries() -> None:
    assert expected_runtime_table_privileges(
        "platform_tasks", immutable=False
    ) == frozenset({"SELECT", "INSERT", "UPDATE"})
    assert expected_runtime_table_privileges(
        "platform_task_events", immutable=True
    ) == frozenset({"SELECT", "INSERT"})
    assert expected_runtime_table_privileges(
        "workspace_commercial_contracts", immutable=False
    ) == frozenset({"SELECT", "INSERT", "UPDATE"})
    assert expected_runtime_table_privileges(
        "commercial_offer_versions", immutable=True
    ) == frozenset({"SELECT", "INSERT"})
    # Legacy FOR ALL policies still become INSERT/SELECT-only when an
    # immutable trigger marks their evidence table append-only.
    assert expected_runtime_table_privileges(
        "task_events", immutable=True
    ) == frozenset({"SELECT", "INSERT"})


def test_commercial_contract_line_family_is_bound_to_the_offer_family() -> None:
    offers = Base.metadata.tables["commercial_offers"]
    lines = Base.metadata.tables["workspace_contract_lines"]
    offer_unique = next(
        constraint
        for constraint in offers.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_commercial_offer_id_family"
    )
    family_fk = next(
        constraint
        for constraint in lines.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_contract_line_offer_family"
    )

    assert [column.name for column in offer_unique.columns] == ["id", "family"]
    assert [column.name for column in family_fk.columns] == ["offer_id", "offer_family"]
    assert [element.target_fullname for element in family_fk.elements] == [
        "commercial_offers.id",
        "commercial_offers.family",
    ]
    assert lines.c.offer_id.type.__class__.__name__ == "FlexibleUUID"
    assert lines.c.policy_version_id.type.__class__.__name__ == "FlexibleUUID"


def test_rls_inventory_matches_required_orm_tenant_relationships() -> None:
    for table_name in RLS_DIRECT_TENANT_TABLES:
        table = Base.metadata.tables[table_name]
        assert "workspace_id" in table.c
        assert table.c.workspace_id.nullable is False

    for table_name, policy in PARENT_WORKSPACE_POLICIES.items():
        table = Base.metadata.tables[table_name]
        parent = Base.metadata.tables[policy.parent_table]
        child_reference = table.c[policy.child_reference_column]

        assert "workspace_id" not in table.c
        assert child_reference.nullable is False
        assert parent.c.workspace_id.nullable is False
        assert any(
            foreign_key.target_fullname == f"{policy.parent_table}.{policy.parent_reference_column}"
            for foreign_key in child_reference.foreign_keys
        )
        assert all(
            column_name in table.c and column_name in parent.c
            for column_name in policy.shared_reference_columns
        )


def test_every_workspace_table_has_rls_or_a_closed_reviewed_exception() -> None:
    workspace_tables = {
        table.name for table in Base.metadata.tables.values() if "workspace_id" in table.c
    }
    protected = (
        set(RLS_DIRECT_TENANT_TABLES)
        | set(COMMERCIAL_CONTROL_TABLES)
        | set(PLATFORM_TASK_CONTROL_TABLES)
    )
    protected_workspace_tables = {
        table_name
        for table_name in protected
        if "workspace_id" in Base.metadata.tables[table_name].c
    }
    exempt = set(RLS_EXEMPT_DIRECT_TENANT_TABLES)

    assert protected.isdisjoint(exempt)
    assert workspace_tables == protected_workspace_tables | exempt
    assert exempt == {
        "commercial_contract_provider_bindings",
        "error_logs",
        "flows",
        "mfa_challenges",
        "notifications",
        "platform_token_usage",
        "subscriptions",
        "support_access_events",
        "support_access_grants",
        "workspace_memberships",
        "workspace_public_api_tokens",
        "platform_model_usage_ledger",
        "model_provider_call_journal",
        "platform_token_usage_by_task",
        "platform_audit_events",
        "platform_onboarding_milestones",
        "platform_work_items",
        "platform_workspace_assignments",
        "workspace_plan_policy_assignments",
        "plan_policy_audit_events",
        "super_admin_notice_receipt_workspaces",
        "workspace_subscriptions",
        "subscription_events",
        "growth_prospects",
        "platform_inbox_conversations",
        "saas_referral_profiles",
        "partner_memberships",
        "benefit_grants",
        "benefit_ledger_entries",
        "coupon_redemptions",
    }
    for exception in RLS_EXEMPT_DIRECT_TENANT_TABLES.values():
        assert exception.category
        assert exception.reason
        assert len(exception.alternative_controls) >= 2
        assert all(exception.alternative_controls)


def test_identity_tenant_registry_and_composite_bindings_are_declared() -> None:
    assert {"signup_verifications", "workspace_invitations"} <= set(RLS_DIRECT_TENANT_TABLES)
    assert set(IDENTITY_RESOLVER_FUNCTIONS) == {
        "klm_resolve_signup_verification_workspace",
        "klm_resolve_workspace_invitation_workspace",
    }
    assert (
        TENANT_RESOLVER_FUNCTION_SIGNATURES["klm_resolve_signup_verification_workspace"]
        == "public.klm_resolve_signup_verification_workspace(text,uuid)"
    )
    assert (
        TENANT_RESOLVER_FUNCTION_SIGNATURES["klm_resolve_workspace_invitation_workspace"]
        == "public.klm_resolve_workspace_invitation_workspace(uuid,text,uuid)"
    )

    memberships = Base.metadata.tables["workspace_memberships"]
    signup = Base.metadata.tables["signup_verifications"]
    invitation = Base.metadata.tables["workspace_invitations"]
    membership_binding = next(
        constraint
        for constraint in memberships.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_workspace_membership_identity_binding"
    )
    signup_binding = next(
        constraint
        for constraint in signup.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_signup_verification_workspace_user_membership"
    )
    invitation_binding = next(
        constraint
        for constraint in invitation.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_workspace_invitation_membership_identity_binding"
    )

    assert tuple(membership_binding.columns.keys()) == (
        "id",
        "workspace_id",
        "user_id",
    )
    assert tuple(signup_binding.columns.keys()) == ("workspace_id", "user_id")
    assert tuple(invitation_binding.columns.keys()) == (
        "membership_id",
        "workspace_id",
        "user_id",
    )


def test_rls_migration_upgrade_and_downgrade_are_sqlite_noops() -> None:
    migration_path = (
        Path(__file__).parents[2] / "alembic/versions/b1c2d3e4f5a6_enable_klm_oss_workspace_rls.py"
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        fake_op = type(
            "FakeAlembicOp",
            (),
            {"get_bind": lambda _self: connection, "execute": Mock()},
        )()
        tree = ast.parse(migration_path.read_text())
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.ImportFrom) and node.module == "alembic")
        ]
        namespace = {"op": fake_op}
        exec(compile(tree, migration_path, "exec"), namespace)

        assert namespace["down_revision"] == "a0b1c2d3e4f5"
        assert namespace["TENANT_TABLES"] == LEGACY_RLS_TENANT_TABLES
        namespace["upgrade"]()
        namespace["downgrade"]()

    fake_op.execute.assert_not_called()
    engine.dispose()


def test_legal_crm_rls_migration_upgrade_and_downgrade_are_sqlite_noops() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic/versions/c2d3e4f5a6b7_expand_legal_crm_workspace_rls.py"
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        fake_op = type(
            "FakeAlembicOp",
            (),
            {"get_bind": lambda _self: connection, "execute": Mock()},
        )()
        tree = ast.parse(migration_path.read_text())
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.ImportFrom) and node.module == "alembic")
        ]
        namespace = {"op": fake_op}
        exec(compile(tree, migration_path, "exec"), namespace)

        assert namespace["down_revision"] == "b1c2d3e4f5a6"
        assert namespace["POLICY_NAME"] == RLS_POLICY_NAME
        assert namespace["LEGACY_POLICY_NAME"] == LEGACY_RLS_POLICY_NAME
        assert namespace["LEGACY_TENANT_TABLES"] == LEGACY_RLS_TENANT_TABLES
        assert namespace["DIRECT_WORKSPACE_TABLES"] == tuple(
            table
            for table in DOMAIN_DIRECT_TENANT_TABLES
            if table not in FLOW_RUNTIME_DIRECT_TENANT_TABLES
            and table not in FINANCE_CHARGE_DIRECT_TENANT_TABLES
            and table not in FINANCE_RECURRENCE_DIRECT_TENANT_TABLES
            and table not in FEE_ARRANGEMENT_DIRECT_TENANT_TABLES
            and table not in PROCESS_MONITORING_DIRECT_TENANT_TABLES
            and table not in CONTRACT_BILLING_DIRECT_TENANT_TABLES
            # Added by the later client-contract expansion. Keep the
            # historical c2d3 migration's frozen inventory unchanged.
            and table != "client_contracts"
            # Added by pr9k2g5i4d08 and protected by the later
            # su2n5j8k7f31 migration; the historical c2d3 migration must
            # remain immutable and must not be reinterpreted by today's
            # runtime catalog.
            and table != "whatsapp_automation_rule_dispatches"
            # Added by zf3y6v9w0s42 and protected by its forward-only RLS
            # migrations. The historical c2d3 inventory must stay frozen.
            and table
            not in {
                "meta_webhook_automation_policies",
                "meta_webhook_operational_states",
                "meta_webhook_receipts",
                "meta_webhook_subscription_jobs",
                "meta_webhook_subscription_states",
            }
        )
        # ``tasks`` moved to direct tenant isolation in a later expansion;
        # c2d3 must still be asserted against the parent-policy inventory it
        # actually shipped with.
        migration_parent_tables = (
            set(PARENT_WORKSPACE_POLICIES) - {"config_document_versions"}
        ) | {"tasks"}
        assert set(namespace["PARENT_WORKSPACE_POLICIES"]) == migration_parent_tables
        assert namespace["_workspace_predicate"]() == workspace_policy_predicate()
        for table_name in migration_parent_tables:
            if table_name == "tasks":
                assert namespace["PARENT_WORKSPACE_POLICIES"][table_name] == (
                    "matters",
                    "matter_id",
                    "id",
                    (),
                )
            else:
                policy = PARENT_WORKSPACE_POLICIES[table_name]
                assert namespace["PARENT_WORKSPACE_POLICIES"][table_name] == (
                    policy.parent_table,
                    policy.child_reference_column,
                    policy.parent_reference_column,
                    policy.shared_reference_columns,
                )
            if table_name == "tasks":
                task_predicate = namespace["_parent_workspace_predicate"](table_name)
                assert "matters.id = tasks.matter_id" in task_predicate
                assert "matters.workspace_id::text" in task_predicate
            else:
                assert namespace["_parent_workspace_predicate"](
                    table_name
                ) == parent_workspace_policy_predicate(table_name)
        namespace["upgrade"]()
        namespace["downgrade"]()

    fake_op.execute.assert_not_called()
    engine.dispose()


def test_whatsapp_automation_dispatch_rls_migration_is_a_sqlite_noop() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic/versions/su2n5j8k7f31_enforce_whatsapp_automation_dispatch_rls.py"
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        fake_op = type(
            "FakeAlembicOp",
            (),
            {"get_bind": lambda _self: connection, "execute": Mock()},
        )()
        tree = ast.parse(migration_path.read_text())
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.ImportFrom) and node.module == "alembic")
        ]
        namespace = {"op": fake_op}
        exec(compile(tree, migration_path, "exec"), namespace)

        assert namespace["revision"] == "su2n5j8k7f31"
        assert namespace["down_revision"] == "rt1m4i7j6e20"
        namespace["upgrade"]()
        namespace["downgrade"]()

    fake_op.execute.assert_not_called()
    engine.dispose()


def test_webhook_resolver_migration_is_a_sqlite_noop() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic/versions/2b3c4d5e6f70_add_tenant_bound_webhook_resolvers.py"
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        fake_op = type(
            "FakeAlembicOp",
            (),
            {"get_bind": lambda _self: connection, "execute": Mock()},
        )()
        tree = ast.parse(migration_path.read_text())
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.ImportFrom) and node.module == "alembic")
        ]
        namespace = {"op": fake_op}
        exec(compile(tree, migration_path, "exec"), namespace)

        assert namespace["down_revision"] == "2f4a6c8e0b1d"
        namespace["upgrade"]()
        namespace["downgrade"]()

    fake_op.execute.assert_not_called()
    engine.dispose()


def test_flow_runtime_rls_migration_upgrade_and_downgrade_are_sqlite_noops() -> None:
    migration_path = (
        Path(__file__).parents[2] / "alembic/versions/1a2b3c4d5e6f_protect_flow_runtime_with_rls.py"
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        fake_op = type(
            "FakeAlembicOp",
            (),
            {"get_bind": lambda _self: connection, "execute": Mock()},
        )()
        tree = ast.parse(migration_path.read_text())
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.ImportFrom) and node.module == "alembic")
        ]
        namespace = {"op": fake_op}
        exec(compile(tree, migration_path, "exec"), namespace)

        assert namespace["down_revision"] == "0f4e5d6c7b8a"
        assert namespace["TENANT_TABLES"] == FLOW_RUNTIME_DIRECT_TENANT_TABLES
        assert namespace["_workspace_predicate"]() == workspace_policy_predicate()
        namespace["upgrade"]()
        namespace["downgrade"]()

    fake_op.execute.assert_not_called()
    engine.dispose()
