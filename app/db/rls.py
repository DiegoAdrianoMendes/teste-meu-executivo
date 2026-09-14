"""Auditable PostgreSQL RLS inventory and runtime connection guard."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.platform_staff_reassignment import (
    PLATFORM_STAFF_REASSIGNMENT_DB_ACTIONS,
    PLATFORM_STAFF_REASSIGNMENT_SCOPE,
)
from app.db.platform_support import (
    PLATFORM_ACTION_SETTING,
    PLATFORM_ACTOR_SETTING,
    PLATFORM_SCOPE_SETTING,
    PLATFORM_SUPPORT_DB_ACTIONS,
    PLATFORM_SUPPORT_SCOPE,
)
from app.db.tenant import WORKSPACE_SETTING
from app.db.worker_discovery import (
    WORKER_DISCOVERY_SCOPE,
    discovery_action,
    worker_discovery_table_families,
)

LEGACY_RLS_POLICY_NAME = "klm_workspace_isolation"
RLS_POLICY_NAME = "workspace_current_id"
COMMERCIAL_TENANT_READ_POLICY_NAME = "workspace_current_id_select"
COMMERCIAL_PLATFORM_READ_POLICY_NAME = "platform_commercial_read"
COMMERCIAL_PLATFORM_INSERT_POLICY_NAME = "platform_commercial_insert"
COMMERCIAL_PLATFORM_UPDATE_POLICY_NAME = "platform_commercial_update"
COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME = (
    "klm_guard_commercial_portability_update"
)
COMMERCIAL_PORTABILITY_GUARD_TRIGGERS = {
    "commercial_adjustments": "trg_commercial_adjustments_portability_guard",
    "entitlement_bonuses": "trg_entitlement_bonuses_portability_guard",
}
COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY = f"""
BEGIN
    IF current_setting('{PLATFORM_SCOPE_SETTING}', true)
           IS DISTINCT FROM 'commercial_portability'
       OR (
           current_setting('{PLATFORM_ACTION_SETTING}', true)
               IS DISTINCT FROM 'commercial_portability:rebind'
           AND current_setting('{PLATFORM_ACTION_SETTING}', true)
               IS DISTINCT FROM 'commercial_portability:restore_rebind'
       ) THEN
        RETURN NEW;
    END IF;

    IF NEW.version <> OLD.version + 1
       OR NEW.updated_at IS NULL
       OR OLD.updated_at IS NULL
       OR NEW.updated_at <= OLD.updated_at
       OR NEW.updated_at > clock_timestamp() THEN
        RAISE EXCEPTION 'invalid internal commercial portability version'
            USING ERRCODE = '42501';
    END IF;

    IF TG_TABLE_NAME = 'commercial_adjustments' THEN
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.contract_id IS DISTINCT FROM OLD.contract_id
           OR NEW.type IS DISTINCT FROM OLD.type
           OR NEW.value IS DISTINCT FROM OLD.value
           OR NEW.starts_at IS DISTINCT FROM OLD.starts_at
           OR NEW.ends_at IS DISTINCT FROM OLD.ends_at
           OR NEW.remaining_cycles IS DISTINCT FROM OLD.remaining_cycles
           OR NEW.stack_group IS DISTINCT FROM OLD.stack_group
           OR NEW.stackable IS DISTINCT FROM OLD.stackable
           OR NEW.portability IS DISTINCT FROM OLD.portability
           OR NEW.reason IS DISTINCT FROM OLD.reason
           OR NEW.source IS DISTINCT FROM OLD.source
           OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'internal portability cannot alter adjustment economics'
                USING ERRCODE = '42501';
        END IF;
        IF current_setting('{PLATFORM_ACTION_SETTING}', true)
               = 'commercial_portability:restore_rebind' THEN
            IF NOT (
                OLD.portability IN (
                    'preserve',
                    'expire_on_offer_change',
                    'manual_review',
                    'recalculate'
                )
                AND OLD.status IN ('scheduled', 'active')
                AND NEW.status IS NOT DISTINCT FROM OLD.status
                AND OLD.contract_line_id IS NOT NULL
                AND NEW.contract_line_id IS NOT NULL
                AND NEW.contract_line_id IS DISTINCT FROM OLD.contract_line_id
            ) THEN
                RAISE EXCEPTION 'invalid internal restore portability rebind'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NOT (
            (
                OLD.portability = 'expire_on_offer_change'
                AND OLD.status IN ('scheduled', 'active')
                AND NEW.status = 'revoked'
                AND NEW.contract_line_id IS NOT DISTINCT FROM OLD.contract_line_id
            )
            OR (
                OLD.portability = 'preserve'
                AND OLD.status IN ('active', 'scheduled')
                AND NEW.status IS NOT DISTINCT FROM OLD.status
                AND OLD.contract_line_id IS NOT NULL
                AND NEW.contract_line_id IS NOT NULL
                AND NEW.contract_line_id IS DISTINCT FROM OLD.contract_line_id
            )
        ) THEN
            RAISE EXCEPTION 'invalid internal adjustment portability transition'
                USING ERRCODE = '42501';
        END IF;
    ELSIF TG_TABLE_NAME = 'entitlement_bonuses' THEN
        IF current_setting('{PLATFORM_ACTION_SETTING}', true)
               = 'commercial_portability:restore_rebind' THEN
            RAISE EXCEPTION 'restore portability cannot update bonuses'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
           OR NEW.entitlement IS DISTINCT FROM OLD.entitlement
           OR NEW.mode IS DISTINCT FROM OLD.mode
           OR NEW.quantity IS DISTINCT FROM OLD.quantity
           OR NEW.starts_at IS DISTINCT FROM OLD.starts_at
           OR NEW.ends_at IS DISTINCT FROM OLD.ends_at
           OR NEW.renews_each_period IS DISTINCT FROM OLD.renews_each_period
           OR NEW.portability IS DISTINCT FROM OLD.portability
           OR NEW.reason IS DISTINCT FROM OLD.reason
           OR NEW.source IS DISTINCT FROM OLD.source
           OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR OLD.portability <> 'expire_on_offer_change'
           OR OLD.status NOT IN ('scheduled', 'active')
           OR NEW.status <> 'revoked' THEN
            RAISE EXCEPTION 'invalid internal bonus portability transition'
                USING ERRCODE = '42501';
        END IF;
    ELSE
        RAISE EXCEPTION 'internal portability guard attached to unexpected table'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
""".strip()
PLATFORM_TASK_SELECT_POLICY_NAME = "platform_task_select"
PLATFORM_TASK_INSERT_POLICY_NAME = "platform_task_insert"
PLATFORM_TASK_UPDATE_POLICY_NAME = "platform_task_update"
WEBHOOK_RESOLVER_FUNCTIONS = (
    "klm_resolve_zapsign_webhook_workspace",
    "klm_resolve_whatsapp_webhook_workspace",
    "klm_resolve_whatsapp_template_webhook_workspace",
    "klm_resolve_asaas_webhook_workspace",
)
IDENTITY_RESOLVER_FUNCTIONS = (
    "klm_resolve_signup_verification_workspace",
    "klm_resolve_workspace_invitation_workspace",
)
TENANT_RESOLVER_FUNCTION_SIGNATURES = {
    "klm_resolve_zapsign_webhook_workspace": (
        "public.klm_resolve_zapsign_webhook_workspace(uuid,text)"
    ),
    "klm_resolve_whatsapp_webhook_workspace": (
        "public.klm_resolve_whatsapp_webhook_workspace(text)"
    ),
    "klm_resolve_whatsapp_template_webhook_workspace": (
        "public.klm_resolve_whatsapp_template_webhook_workspace(text)"
    ),
    "klm_resolve_asaas_webhook_workspace": (
        "public.klm_resolve_asaas_webhook_workspace(text)"
    ),
    "klm_resolve_signup_verification_workspace": (
        "public.klm_resolve_signup_verification_workspace(text,uuid)"
    ),
    "klm_resolve_workspace_invitation_workspace": (
        "public.klm_resolve_workspace_invitation_workspace(uuid,text,uuid)"
    ),
}
TENANT_RESOLVER_FUNCTIONS = tuple(TENANT_RESOLVER_FUNCTION_SIGNATURES)
RESOLVER_OWNER_POLICY_NAME = "klm_resolver_owner_select"
WORKER_DISCOVERY_POLICY_NAME = "klm_worker_discovery_select"
RESOLVER_OWNER_ROLE_MARKER = "__klm_resolver_owner__"
RESOLVER_OWNER_LOOKUP_TABLES = frozenset(
    {
        "matters",
        "whatsapp_connections",
        "signup_verifications",
        "workspace_invitations",
        "integration_connections",
    }
)
TENANT_RESOLVER_FUNCTION_BODIES = {
    "klm_resolve_zapsign_webhook_workspace": """
        SELECT matter.workspace_id
        FROM public.matters AS matter
        WHERE matter.id = p_matter_id
          AND matter.zapsign_doc_token = p_document_token
          AND matter.deleted_at IS NULL
    """,
    "klm_resolve_whatsapp_webhook_workspace": """
        SELECT connection.workspace_id
        FROM public.whatsapp_connections AS connection
        WHERE connection.phone_number_id = p_phone_number_id
    """,
    "klm_resolve_whatsapp_template_webhook_workspace": """
        SELECT CASE
            WHEN COUNT(DISTINCT connection.workspace_id) = 1
            THEN MIN(connection.workspace_id::text)::uuid
            ELSE NULL::uuid
        END
        FROM public.whatsapp_connections AS connection
        WHERE connection.waba_id = p_waba_id
    """,
    "klm_resolve_asaas_webhook_workspace": """
        SELECT CASE
            WHEN COUNT(DISTINCT connection.workspace_id) = 1
            THEN MIN(connection.workspace_id::text)::uuid
            ELSE NULL::uuid
        END
        FROM public.integration_connections AS connection
        WHERE connection.provider = 'asaas'
          AND connection.status <> 'disconnected'
          AND connection.settings->>'webhookSecretDigest' = p_token_digest
    """,
    "klm_resolve_signup_verification_workspace": """
        SELECT verification.workspace_id
        FROM public.signup_verifications AS verification
        WHERE ((p_token_digest IS NOT NULL) <> (p_delivery_id IS NOT NULL))
          AND (
                verification.token_digest = p_token_digest
                OR EXISTS (
                    SELECT 1
                    FROM public.identity_outbox_messages AS delivery
                    WHERE delivery.id = p_delivery_id
                      AND delivery.related_id = verification.id
                      AND delivery.related_type = 'signup_verification'
                      AND delivery.purpose = 'signup_verification'
                )
          )
        LIMIT 1
    """,
    "klm_resolve_workspace_invitation_workspace": """
        SELECT invitation.workspace_id
        FROM public.workspace_invitations AS invitation
        WHERE invitation.id = p_invitation_id
          AND ((p_token_digest IS NOT NULL) <> (p_delivery_id IS NOT NULL))
          AND (
                invitation.token_digest = p_token_digest
                OR EXISTS (
                    SELECT 1
                    FROM public.identity_outbox_messages AS delivery
                    WHERE delivery.id = p_delivery_id
                      AND delivery.related_id = invitation.id
                      AND delivery.related_type = 'workspace_invitation'
                      AND delivery.purpose = 'workspace_invitation'
                )
          )
        LIMIT 1
    """,
}
TENANT_RESOLVER_STRICTNESS = {
    "klm_resolve_zapsign_webhook_workspace": True,
    "klm_resolve_whatsapp_webhook_workspace": True,
    "klm_resolve_whatsapp_template_webhook_workspace": True,
    "klm_resolve_asaas_webhook_workspace": True,
    "klm_resolve_signup_verification_workspace": False,
    "klm_resolve_workspace_invitation_workspace": False,
}
EXPECTED_POLICY_CATALOG_PREDICATE = (
    f"((workspace_id)::text = NULLIF(current_setting('{WORKSPACE_SETTING}'::text, true), ''::text))"
)

KLM_LEGACY_TENANT_TABLES = (
    "klm_area_blueprints",
    "klm_omnichannel_threads",
    "klm_omnichannel_messages",
    "klm_follow_up_tasks",
    "klm_acquisition_events",
    "klm_technical_requests",
    "klm_audit_events",
    "klm_external_action_intents",
)

KLM_NATIVE_EXTENSION_TENANT_TABLES = (
    "klm_email_ingest_receipts",
    "klm_legal_artifact_versions",
    "klm_legal_artifact_states",
    "klm_process_monitoring_records",
)

NATIVE_GROWTH_DIRECT_TENANT_TABLES = (
    "klm_ads_conversion_feedback_records",
    "klm_ads_manager_entities",
    "klm_ads_manager_import_receipts",
    "klm_ads_metric_observations",
    "klm_lead_hydration_events",
    "klm_lead_hydration_jobs",
    "whatsapp_audience_list_members",
    "whatsapp_audience_lists",
)
KLM_NATIVE_TENANT_TABLES = KLM_LEGACY_TENANT_TABLES + KLM_NATIVE_EXTENSION_TENANT_TABLES
KLM_TENANT_TABLES = KLM_NATIVE_TENANT_TABLES

OSS_TENANT_TABLES = (
    "oss_external_entity_links",
    "oss_integration_event_receipts",
)

OSS_NATIVE_TENANT_TABLES = (
    "oss_native_capability_states",
    "oss_native_commands",
    "oss_native_audit_events",
    "oss_native_records",
    "oss_native_outbox_events",
    "oss_function_unit_events",
)

OMNICHANNEL_V6_DIRECT_TENANT_TABLES = (
    "klm_automation_trigger_receipts",
    "klm_lead_capture_receipts",
    "klm_omnichannel_action_intents",
    "klm_omnichannel_action_intent_events",
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
    "klm_omnichannel_provider_media_receipts",
    "klm_omnichannel_provider_media_upload_intents",
    "klm_omnichannel_provider_media_upload_reconciliations",
    "klm_omnichannel_public_ingress_receipts",
    "klm_omnichannel_routing_events",
    "klm_omnichannel_scheduled_messages",
    "klm_omnichannel_scheduled_message_events",
    "klm_omnichannel_thread_bindings",
    "klm_omnichannel_webhook_credentials",
    "klm_workspace_team_members",
    "klm_workspace_teams",
)

META_GROUP_DIRECT_TENANT_TABLES = (
    "klm_meta_group_capabilities",
    "klm_meta_groups",
    "klm_meta_group_participants",
    "klm_meta_group_operations",
    "klm_meta_group_lifecycle_events",
)

CONVERSATION_COLLABORATION_DIRECT_TENANT_TABLES = (
    "klm_conversation_groups",
    "klm_conversation_group_participants",
    "klm_conversation_mention_operations",
    "klm_conversation_mentions",
    "klm_conversation_message_references",
)

STAFF_AVAILABILITY_DIRECT_TENANT_TABLES = (
    "staff_availability_audit_events",
    "staff_availability_change_requests",
    "staff_availability_exceptions",
    "staff_availability_profiles",
    "staff_recurring_availability",
)

STAFF_REASSIGNMENT_DIRECT_TENANT_TABLES = (
    "workspace_staff_reassignment_plans",
    "workspace_staff_reassignment_items",
    "workspace_staff_reassignment_events",
)

PLATFORM_STAFF_REASSIGNMENT_TABLES = (
    "platform_staff_reassignment_plans",
    "platform_staff_reassignment_items",
    "platform_staff_reassignment_events",
)

# Enabled by its own self-contained migration
# (a4c6e8f0b215_add_whatsapp_connection_tiers.py), not by the historical
# c2d3e4f5a6b7 legal/CRM RLS expansion, so it is tracked separately rather
# than appended to CHANNEL_DIRECT_TENANT_TABLES (which mirrors that
# migration's frozen DIRECT_WORKSPACE_TABLES tuple 1:1).
WHATSAPP_CONNECTION_TIER_DIRECT_TENANT_TABLES = ("whatsapp_connection_tiers",)

# Habilitada por migração própria e self-contained (BYOK de LLM por
# workspace), então é rastreada separadamente das tuplas históricas
# congeladas — mesmo racional de WHATSAPP_CONNECTION_TIER acima.
WORKSPACE_MODEL_CREDENTIAL_DIRECT_TENANT_TABLES = ("workspace_model_credentials",)

# Roteamento de modelos de IA por classe de tarefa (S4). Também habilitada
# por migração própria e self-contained, rastreada separadamente pelo mesmo
# racional das credenciais BYOK acima.
WORKSPACE_AI_MODEL_ROUTING_DIRECT_TENANT_TABLES = ("workspace_ai_model_routes",)

# Telemetria SQL de fallback do gateway. Não é ledger financeiro e sempre
# permanece limitada ao workspace corrente.
AI_GATEWAY_EVENT_DIRECT_TENANT_TABLES = ("ai_gateway_events",)

SUPPORT_DIRECT_TENANT_TABLES = (
    "support_tickets",
    "support_ticket_collaborators",
    "support_ticket_messages",
    "support_ticket_attachments",
    "support_ticket_events",
    "support_notification_outbox",
    # F15 — cópia da conversa por e-mail ao encerrar o chamado.
    "support_transcript_emails",
)

LEGACY_RLS_TENANT_TABLES = KLM_LEGACY_TENANT_TABLES + OSS_TENANT_TABLES

LEGAL_CRM_DIRECT_TENANT_TABLES = (
    "clients",
    "client_contracts",
    "conversation_insights",
    "finance_entries",
    "finance_reminders",
    "integration_connections",
    "knowledge_chunks",
    "matters",
    "meetings",
)

MEETING_COLLABORATION_DIRECT_TENANT_TABLES = (
    "meeting_creation_receipts",
    "meeting_notification_outbox",
    "meeting_participants",
)

TASK_COLLABORATION_DIRECT_TENANT_TABLES = (
    "tasks",
    "task_attachments",
    "task_checklist_items",
    "task_checklists",
    "task_external_shares",
    "task_dependencies",
    "task_events",
    "task_label_assignments",
    "task_labels",
    "task_list_memberships",
    "task_lists",
    "task_mentions",
    "task_participants",
    "task_review_rounds",
    "task_board_columns",
    "task_board_placements",
    "task_boards",
)

TASK_REMINDER_DIRECT_TENANT_TABLES = (
    "task_reminder_command_receipts",
    "task_reminder_events",
    "task_reminder_outbox",
    "task_reminder_rules",
)

# Catálogo e contratos possuem políticas compostas: a conta lê somente seu
# contrato e suas exceções; o Blackbox usa um contexto atribuível por módulo e
# ação. As tabelas globais de oferta também podem expor apenas versões públicas
# ativas ao tenant autenticado.
COMMERCIAL_CONTROL_TABLES = (
    "commercial_adjustments",
    "commercial_catalog_events",
    "commercial_command_receipts",
    "commercial_offer_versions",
    "commercial_offers",
    "entitlement_bonuses",
    "workspace_commercial_contracts",
    "workspace_contract_lines",
)

# These six tables form the tenant-facing capability manifest.  A workspace
# may read its own effective commercial state, but it must never be able to
# mutate the control-plane source of truth directly.  Events and idempotency
# receipts deliberately remain platform-only.
COMMERCIAL_TENANT_READ_TABLES = (
    "commercial_adjustments",
    "commercial_offer_versions",
    "commercial_offers",
    "entitlement_bonuses",
    "workspace_commercial_contracts",
    "workspace_contract_lines",
)
COMMERCIAL_PLATFORM_ONLY_TABLES = tuple(
    table_name
    for table_name in COMMERCIAL_CONTROL_TABLES
    if table_name not in COMMERCIAL_TENANT_READ_TABLES
)

# Tarefas internas nunca usam o contexto de um workspace cliente. A decisão
# fina por linha permanece no serviço, e a política do banco exige que a query
# tenha atravessado o RBAC Blackbox com ator, módulo e ação explícitos.
PLATFORM_TASK_CONTROL_TABLES = (
    "platform_task_events",
    "platform_task_participants",
    "platform_task_review_rounds",
    "platform_tasks",
)

FINANCE_CHARGE_DIRECT_TENANT_TABLES = (
    "finance_charges",
    "asaas_webhook_receipts",
    "finance_dunning_log",
)

FINANCE_RECURRENCE_DIRECT_TENANT_TABLES = (
    "finance_recurrences",
    "finance_recurrence_runs",
)

FEE_ARRANGEMENT_DIRECT_TENANT_TABLES = (
    "fee_arrangements",
    "time_entries",
)

# F11 — contrato → nós financeiros com aprovação: proposta extraída sempre
# nasce draft; RLS direta por workspace_id, igual às demais tabelas F9/F3.
CONTRACT_BILLING_DIRECT_TENANT_TABLES = ("contract_billing_proposals",)

BANK_RECONCILIATION_DIRECT_TENANT_TABLES = (
    "bank_transactions",
    "reconciliation_matches",
)

# Módulo processual contratável à parte (add-on pago, não core): monitoramento
# DJEN/DataJud com quota por `EntitlementKey.monitored_processes`. RLS direta
# por workspace_id, mesmo padrão de F9 (fee_arrangements) acima.
PROCESS_MONITORING_DIRECT_TENANT_TABLES = (
    "monitored_processes",
    "process_publications",
)

# Central de Exceções (Plano Mestre §6/§3) — generaliza a fila de
# aprovações (`approval_queue_items`, acima) para todo tipo de exceção
# humana nomeada no plano. Tabela nova (não reaproveita
# approval_queue_items) porque matter_id é opcional aqui — várias exceções
# nascem sem vínculo confiável a nenhum caso. RLS direta por workspace_id,
# mesmo padrão de FinanceEntry/monitored_processes acima.
EXCEPTION_CENTER_DIRECT_TENANT_TABLES = ("legal_exceptions",)

# F14 — harmonização de documentos financeiros × lançamentos manuais:
# estende a conciliação bancária (F7 acima) para qualquer documento
# (PDF/XML/CSV), sempre diagnóstica — RLS direta por workspace_id, mesmo
# padrão de F7.
FINANCE_HARMONIZATION_DIRECT_TENANT_TABLES = (
    "finance_harmonization_runs",
    "finance_harmonization_items",
    "finance_harmonization_quotas",
)

# Onda 3 — Importador Universal de Migração (Plano Mestre §12). RLS direta
# por workspace_id, mesmo padrão de EXCEPTION_CENTER_DIRECT_TENANT_TABLES
# acima (job/staging/divergência nascem sem vínculo confiável a nenhum
# Matter até a importação decidir).
MIGRATION_DIRECT_TENANT_TABLES = (
    "migration_jobs",
    "migration_staging_records",
    "migration_divergences",
)

# Onda 4 — Controladoria jurídica (Plano Mestre §8): distribuição
# configurável de trabalho jurídico (LegalDemand) + histórico de decisão, e
# escalonamento de SLA em camadas. RLS direta por workspace_id, mesmo
# padrão de EXCEPTION_CENTER_DIRECT_TENANT_TABLES/MIGRATION_DIRECT_TENANT_TABLES
# acima.
LEGAL_CONTROLADORIA_DIRECT_TENANT_TABLES = (
    "distribution_rules",
    "distribution_decisions",
    "sla_escalation_logs",
)

# Onda 5 — Matriz Estratégia Fato–Prova–Pedido (Plano Mestre §3 item 8):
# fato alegado, vínculo fato↔prova e pedido, todos ligados a uma
# LegalDemand. RLS direta por workspace_id, mesmo padrão de
# LEGAL_CONTROLADORIA_DIRECT_TENANT_TABLES acima (legal_demand_id é
# denormalizado nas três tabelas, não um vínculo indireto via parent
# policy).
CASE_STRATEGY_DIRECT_TENANT_TABLES = (
    "case_facts",
    "case_proof_links",
    "case_pleadings",
)

# Onda 5 — Caso 360 canônico (Plano Mestre §5): parte do caso além do
# cliente titular (polo/papel/qualificação), ligada a uma LegalDemand.
# RLS direta por workspace_id, mesmo padrão de CASE_STRATEGY_DIRECT_TENANT_TABLES
# acima. A prova/evidência do Caso 360 reusa CASE_STRATEGY_DIRECT_TENANT_TABLES
# (case_proof_links) — não há tabela de evidência própria aqui.
CASE_360_DIRECT_TENANT_TABLES = ("case_parties",)

# Onda 6 — Produção jurídica (§9/§3 item 20): veredito de conferência de
# citação (súmula/tema/REsp/lei) e achado da revisão adversarial sobre uma
# LegalArtifactVersion. RLS direta por workspace_id, mesmo padrão de
# CASE_STRATEGY_DIRECT_TENANT_TABLES acima — legal_demand_id é denormalizado
# pelo mesmo motivo (ver app/models/legal_artifact_verification.py).
LEGAL_PRODUCTION_DIRECT_TENANT_TABLES = (
    "legal_citation_verifications",
    "legal_review_findings",
)

# Onda 7 — DMS jurídico (Plano Mestre §11): asset canônico deduplicado por
# hash, referência de cada ingestão (upload/e-mail/WhatsApp/espelho de
# sincronização), link de compartilhamento com expiração e trilha de
# download. RLS direta por workspace_id, mesmo padrão de
# LEGAL_PRODUCTION_DIRECT_TENANT_TABLES acima.
LEGAL_DMS_DIRECT_TENANT_TABLES = (
    "document_assets",
    "document_asset_references",
    "document_share_links",
    "document_share_downloads",
)

# Onda 7 — Pasta de protocolo + manifesto (Plano Mestre §10): projeção
# mutável que amarra Caso 360 + artefato aprovado (Onda 6) numa pasta
# numerada, com o manifesto de conformidade e o registro POSTERIOR do
# recibo (ver app/models/protocol_folder.py). RLS direta por workspace_id,
# mesmo padrão de LEGAL_PRODUCTION_DIRECT_TENANT_TABLES acima.
PROTOCOL_FOLDER_DIRECT_TENANT_TABLES = ("protocol_folders",)

# Onda 8 — Operação Massificada / Lotes (Plano Mestre §7): lote agrupado
# por critério (cliente/carteira/tribunal/matéria/tipo publicação/fase/
# tese/responsável/prazo/playbook) e cada item com estado individual
# próprio (nunca escondido pelo agregado do lote). RLS direta por
# workspace_id, mesmo padrão de PROTOCOL_FOLDER_DIRECT_TENANT_TABLES acima
# (ver app/models/legal_batch.py).
LEGAL_BATCH_DIRECT_TENANT_TABLES = ("legal_batches", "legal_batch_items")

REFERRAL_DIRECT_TENANT_TABLES = (
    "referral_partners",
    "matter_referrals",
    "referral_commissions",
)

CHANNEL_DIRECT_TENANT_TABLES = (
    "meta_approved_messages",
    "meta_automation_rules",
    "meta_channel_campaigns",
    "meta_channel_connections",
    "meta_conversation_notes",
    "meta_social_conversations",
    "meta_webhook_automation_policies",
    "meta_webhook_operational_states",
    "meta_webhook_receipts",
    "meta_webhook_subscription_jobs",
    "meta_webhook_subscription_states",
    "whatsapp_automation_rule_dispatches",
    "whatsapp_campaign_recipients",
    "whatsapp_campaign_send_jobs",
    "whatsapp_campaigns",
    "whatsapp_connections",
    "whatsapp_contacts",
    "whatsapp_conversation_notes",
    "whatsapp_meta_templates",
    "whatsapp_opt_in_records",
    "whatsapp_suppression_entries",
)

FLOW_RUNTIME_DIRECT_TENANT_TABLES = (
    "agent_runs",
    "flow_runs",
    "human_overrides",
    "scheduled_messages",
)

AGENT_PLATFORM_DIRECT_TENANT_TABLES = (
    "agent_deployments",
    "flow_node_definitions",
    "human_decisions",
    "human_notification_deliveries",
)

FINANCE_DIMENSIONAL_DIRECT_TENANT_TABLES = (
    "finance_cost_centers",
    "finance_allocations",
    "finance_forecasts",
    "finance_valuations",
    "finance_tax_profiles",
    "finance_tax_reform_alerts",
    "finance_export_jobs",
    "finance_budget_lines",
)

ADDITIONAL_DIRECT_TENANT_TABLES = (
    # Plano Mestre §14 — política de autonomia A0-A4 por evento.
    "agent_autonomy_policies",
    "executive_profiles",
    "executive_threads",
    "executive_thread_shares",
    "executive_rounds",
    "executive_source_receipts",
    "executive_messages",
    "executive_reports",
    "booking_links",
    "commercial_account_requests",
    "commercial_checkout_intents",
    "config_documents",
    "entitlement_usage_reservations",
    "klm_knowledge_article_feedback",
    "legal_demands",
    "matter_treatment_estimates",
    "playbook_scope_bindings",
    "playbook_simulation_runs",
    "playbook_version_governance",
    "public_api_mutation_receipts",
    "public_api_request_logs",
    "storage_objects",
    "subscription_access_states",
    "subscription_access_transitions",
    "token_budgets",
    "signup_verifications",
    "workspace_ai_token_package_requests",
    "workspace_entitlement_overrides",
    "workspace_monthly_entitlement_usage",
    "workspace_invitations",
)


@dataclass(frozen=True)
class TenantIsolationException:
    """Classify a pre-workspace or control-plane table outside tenant RLS."""

    category: str
    reason: str
    alternative_controls: tuple[str, ...]


# These tables cannot use the normal one-workspace transaction policy because
# they are queried before a workspace is selected or by the cross-workspace SaaS
# control plane. The closed registry makes every exception reviewable and keeps
# newly added workspace tables from silently escaping classification.
RLS_EXEMPT_DIRECT_TENANT_TABLES = {
    "commercial_contract_provider_bindings": TenantIsolationException(
        category="signed_provider_control_plane",
        reason=(
            "A signed Stripe webhook must resolve a subscription to its workspace "
            "before a tenant database context exists."
        ),
        alternative_controls=(
            "stripe_hmac_verification",
            "opaque_provider_identifiers",
            "workspace_rebinding_before_contract_mutation",
        ),
    ),
    "error_logs": TenantIsolationException(
        category="platform_control_plane",
        reason="Global and workspace error triage is restricted to platform staff.",
        alternative_controls=("platform_rbac", "tenant_admin_denial", "sanitized_payloads"),
    ),
    "flows": TenantIsolationException(
        category="shared_catalog_and_tenant",
        reason="Global flow templates and workspace flows currently share one nullable column.",
        alternative_controls=(
            "workspace_filtered_mutations",
            "global_template_read_only",
            "optimistic_revision",
        ),
    ),
    "mfa_challenges": TenantIsolationException(
        category="identity_bootstrap",
        reason="MFA is resolved before a workspace is selected.",
        alternative_controls=("user_binding", "purpose_binding", "expiry", "attempt_limit"),
    ),
    "notifications": TenantIsolationException(
        category="shared_platform_and_tenant",
        reason="Platform notifications and tenant notifications share one nullable column.",
        alternative_controls=("recipient_binding", "workspace_filter", "platform_rbac"),
    ),
    "platform_token_usage": TenantIsolationException(
        category="platform_control_plane",
        reason="The SaaS control plane aggregates usage across workspaces.",
        alternative_controls=("platform_rbac", "workspace_filtered_tenant_reads"),
    ),
    "platform_token_usage_by_task": TenantIsolationException(
        category="platform_control_plane",
        reason="The SaaS control plane aggregates model usage by task across workspaces.",
        alternative_controls=("platform_rbac", "workspace_filtered_tenant_reads"),
    ),
    "platform_audit_events": TenantIsolationException(
        category="platform_control_plane",
        reason="The append-only administrative audit spans platform-managed workspaces.",
        alternative_controls=("platform_rbac", "append_only_guards", "actor_binding"),
    ),
    "platform_onboarding_milestones": TenantIsolationException(
        category="platform_control_plane",
        reason="The onboarding operations queue coordinates milestones across workspace accounts.",
        alternative_controls=(
            "platform_rbac",
            "workspace_filtered_commands",
            "optimistic_revision",
        ),
    ),
    "platform_work_items": TenantIsolationException(
        category="platform_control_plane",
        reason=(
            "The operational work-item projection intentionally aggregates "
            "authorized cross-workspace queues."
        ),
        alternative_controls=(
            "platform_rbac",
            "projection_only",
            "source_command_revalidation",
        ),
    ),
    "platform_workspace_assignments": TenantIsolationException(
        category="platform_control_plane",
        reason="Platform staff assignments coordinate responsibility across workspace accounts.",
        alternative_controls=(
            "platform_rbac",
            "workspace_filtered_commands",
            "append_only_audit",
        ),
    ),
    "platform_model_usage_ledger": TenantIsolationException(
        category="platform_control_plane",
        reason="The gateway stores append-only model usage receipts across workspaces.",
        alternative_controls=(
            "gateway_workspace_binding",
            "platform_rbac",
            "append_only_guards",
        ),
    ),
    "model_provider_call_journal": TenantIsolationException(
        category="platform_control_plane",
        reason="The canonical gateway owns provider executions across workspaces.",
        alternative_controls=(
            "gateway_workspace_binding",
            "unique_workspace_request_claim",
            "platform_rbac",
        ),
    ),
    "workspace_plan_policy_assignments": TenantIsolationException(
        category="platform_control_plane",
        reason="Plan rollouts are governed by Super Admin across workspace accounts.",
        alternative_controls=(
            "platform_rbac",
            "workspace_filtered_resolution",
            "append_only_audit",
        ),
    ),
    "plan_policy_audit_events": TenantIsolationException(
        category="platform_control_plane",
        reason="Plan policy audit is a cross-workspace immutable control-plane log.",
        alternative_controls=("platform_rbac", "append_only_guards", "actor_binding"),
    ),
    "super_admin_notice_receipt_workspaces": TenantIsolationException(
        category="platform_control_plane",
        reason="Notice receipts fan out a platform notice to selected workspaces.",
        alternative_controls=("platform_rbac", "recipient_binding", "workspace_filter"),
    ),
    "subscriptions": TenantIsolationException(
        category="billing_bootstrap",
        reason="Signed billing webhooks resolve a workspace from provider identifiers.",
        alternative_controls=("verified_webhook", "unique_workspace_binding", "event_receipt"),
    ),
    "workspace_subscriptions": TenantIsolationException(
        category="platform_control_plane",
        reason="A camada comercial do Super Admin governa assinaturas por escritório.",
        alternative_controls=("platform_rbac", "workspace_filtered_tenant_reads"),
    ),
    "subscription_events": TenantIsolationException(
        category="platform_control_plane",
        reason="Auditoria imutável de mudanças de assinatura entre escritórios (Super Admin).",
        alternative_controls=("platform_rbac", "append_only_guards", "actor_binding"),
    ),
    "growth_prospects": TenantIsolationException(
        category="platform_control_plane",
        reason="O CRM de aquisição da plataforma gere escritórios-alvo (Super Admin).",
        alternative_controls=("platform_rbac", "workspace_filtered_tenant_reads"),
    ),
    "platform_inbox_conversations": TenantIsolationException(
        category="platform_control_plane",
        reason=(
            "A inbox pertence aos canais oficiais do SaaS; workspace_id é apenas "
            "um vínculo opcional com a conta convertida, não o proprietário tenant "
            "da conversa."
        ),
        alternative_controls=(
            "platform_rbac",
            "public_ingress_token_binding",
            "workspace_link_not_tenant_ownership",
        ),
    ),
    "saas_referral_profiles": TenantIsolationException(
        category="platform_benefits_program",
        reason=(
            "Políticas RLS próprias criadas pela migração 8d3ee1871532, com bypass "
            "explícito somente no contexto da plataforma "
            "(set_platform_benefits_context)."
        ),
        alternative_controls=(
            "migration_managed_rls_policies",
            "platform_scoped_bypass_context",
            "immutable_ledger_audit",
        ),
    ),
    "partner_memberships": TenantIsolationException(
        category="platform_benefits_program",
        reason=(
            "Políticas RLS próprias criadas pela migração 8d3ee1871532, com bypass "
            "explícito somente no contexto da plataforma "
            "(set_platform_benefits_context)."
        ),
        alternative_controls=(
            "migration_managed_rls_policies",
            "platform_scoped_bypass_context",
            "immutable_ledger_audit",
        ),
    ),
    "benefit_grants": TenantIsolationException(
        category="platform_benefits_program",
        reason=(
            "Políticas RLS próprias criadas pela migração 8d3ee1871532, com bypass "
            "explícito somente no contexto da plataforma "
            "(set_platform_benefits_context)."
        ),
        alternative_controls=(
            "migration_managed_rls_policies",
            "platform_scoped_bypass_context",
            "immutable_ledger_audit",
        ),
    ),
    "benefit_ledger_entries": TenantIsolationException(
        category="platform_benefits_program",
        reason=(
            "Políticas RLS próprias criadas pela migração 8d3ee1871532, com bypass "
            "explícito somente no contexto da plataforma "
            "(set_platform_benefits_context)."
        ),
        alternative_controls=(
            "migration_managed_rls_policies",
            "platform_scoped_bypass_context",
            "immutable_ledger_audit",
        ),
    ),
    "coupon_redemptions": TenantIsolationException(
        category="platform_benefits_program",
        reason=(
            "Políticas RLS próprias criadas pela migração 8d3ee1871532, com bypass "
            "explícito somente no contexto da plataforma "
            "(set_platform_benefits_context)."
        ),
        alternative_controls=(
            "migration_managed_rls_policies",
            "platform_scoped_bypass_context",
            "immutable_ledger_audit",
        ),
    ),
    "support_access_events": TenantIsolationException(
        category="support_control_plane",
        reason="Platform support audit spans the grantor and target workspace.",
        alternative_controls=("platform_rbac", "scoped_grant", "append_only"),
    ),
    "support_access_grants": TenantIsolationException(
        category="support_control_plane",
        reason="Break-glass grants are issued before entering the target workspace.",
        alternative_controls=("platform_rbac", "scope_allowlist", "expiry", "revocation"),
    ),
    "workspace_memberships": TenantIsolationException(
        category="identity_bootstrap",
        reason="Memberships are needed to discover and select an allowed workspace.",
        alternative_controls=("current_user_binding", "active_status", "token_membership_binding"),
    ),
    "workspace_public_api_tokens": TenantIsolationException(
        category="public_api_bootstrap",
        reason="A token digest resolves its workspace before tenant context exists.",
        alternative_controls=("token_digest", "workspace_binding", "revocation", "expiry"),
    ),
}


@dataclass(frozen=True)
class ParentWorkspacePolicy:
    """Describe a child table whose workspace is inherited from one parent."""

    parent_table: str
    child_reference_column: str
    parent_reference_column: str = "id"
    shared_reference_columns: tuple[str, ...] = ()


PARENT_WORKSPACE_POLICIES = {
    "approval_queue_items": ParentWorkspacePolicy("matters", "matter_id"),
    "campaign_attributions": ParentWorkspacePolicy("matters", "matter_id"),
    "card_field_changes": ParentWorkspacePolicy("matters", "matter_id"),
    "card_scores": ParentWorkspacePolicy("matters", "matter_id"),
    "conversations": ParentWorkspacePolicy("matters", "matter_id"),
    "config_document_versions": ParentWorkspacePolicy("config_documents", "document_id"),
    "documents": ParentWorkspacePolicy("matters", "matter_id"),
    "honorarium_estimates": ParentWorkspacePolicy("matters", "matter_id"),
    "meta_social_messages": ParentWorkspacePolicy("meta_social_conversations", "conversation_id"),
    "revenue_projections": ParentWorkspacePolicy("matters", "matter_id"),
    "timeline_events": ParentWorkspacePolicy("matters", "matter_id"),
    "whatsapp_campaign_audiences": ParentWorkspacePolicy("whatsapp_campaigns", "campaign_id"),
    "whatsapp_campaign_delivery_events": ParentWorkspacePolicy(
        "whatsapp_campaign_recipients",
        "recipient_id",
        shared_reference_columns=("campaign_id",),
    ),
    "whatsapp_campaign_template_snapshots": ParentWorkspacePolicy(
        "whatsapp_campaigns", "campaign_id"
    ),
    "whatsapp_messages": ParentWorkspacePolicy(
        "whatsapp_contacts",
        "contact_id",
        shared_reference_columns=("whatsapp_connection_id",),
    ),
}

DOMAIN_DIRECT_TENANT_TABLES = (
    LEGAL_CRM_DIRECT_TENANT_TABLES
    + FINANCE_CHARGE_DIRECT_TENANT_TABLES
    + FINANCE_RECURRENCE_DIRECT_TENANT_TABLES
    + FEE_ARRANGEMENT_DIRECT_TENANT_TABLES
    + PROCESS_MONITORING_DIRECT_TENANT_TABLES
    + CONTRACT_BILLING_DIRECT_TENANT_TABLES
    + CHANNEL_DIRECT_TENANT_TABLES
    + FLOW_RUNTIME_DIRECT_TENANT_TABLES
)
RLS_DIRECT_TENANT_TABLES = (
    LEGACY_RLS_TENANT_TABLES
    + DOMAIN_DIRECT_TENANT_TABLES
    + ADDITIONAL_DIRECT_TENANT_TABLES
    + KLM_NATIVE_EXTENSION_TENANT_TABLES
    + NATIVE_GROWTH_DIRECT_TENANT_TABLES
    + OSS_NATIVE_TENANT_TABLES
    + OMNICHANNEL_V6_DIRECT_TENANT_TABLES
    + META_GROUP_DIRECT_TENANT_TABLES
    + CONVERSATION_COLLABORATION_DIRECT_TENANT_TABLES
    + STAFF_AVAILABILITY_DIRECT_TENANT_TABLES
    + STAFF_REASSIGNMENT_DIRECT_TENANT_TABLES
    + WHATSAPP_CONNECTION_TIER_DIRECT_TENANT_TABLES
    + WORKSPACE_MODEL_CREDENTIAL_DIRECT_TENANT_TABLES
    + WORKSPACE_AI_MODEL_ROUTING_DIRECT_TENANT_TABLES
    + AI_GATEWAY_EVENT_DIRECT_TENANT_TABLES
    + SUPPORT_DIRECT_TENANT_TABLES
    + REFERRAL_DIRECT_TENANT_TABLES
    + BANK_RECONCILIATION_DIRECT_TENANT_TABLES
    + FINANCE_HARMONIZATION_DIRECT_TENANT_TABLES
    + EXCEPTION_CENTER_DIRECT_TENANT_TABLES
    + MIGRATION_DIRECT_TENANT_TABLES
    + LEGAL_CONTROLADORIA_DIRECT_TENANT_TABLES
    + CASE_STRATEGY_DIRECT_TENANT_TABLES
    + CASE_360_DIRECT_TENANT_TABLES
    + LEGAL_PRODUCTION_DIRECT_TENANT_TABLES
    + LEGAL_DMS_DIRECT_TENANT_TABLES
    + PROTOCOL_FOLDER_DIRECT_TENANT_TABLES
    + LEGAL_BATCH_DIRECT_TENANT_TABLES
    + TASK_COLLABORATION_DIRECT_TENANT_TABLES
    + TASK_REMINDER_DIRECT_TENANT_TABLES
    + MEETING_COLLABORATION_DIRECT_TENANT_TABLES
    + AGENT_PLATFORM_DIRECT_TENANT_TABLES
    + FINANCE_DIMENSIONAL_DIRECT_TENANT_TABLES
)
RLS_PARENT_TENANT_TABLES = tuple(PARENT_WORKSPACE_POLICIES)
RLS_TENANT_TABLES = (
    RLS_DIRECT_TENANT_TABLES
    + RLS_PARENT_TENANT_TABLES
    + PLATFORM_STAFF_REASSIGNMENT_TABLES
    + COMMERCIAL_CONTROL_TABLES
    + PLATFORM_TASK_CONTROL_TABLES
)


def workspace_policy_predicate() -> str:
    """Return the fail-closed predicate used by direct workspace policies."""
    return f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"


def worker_discovery_policy_predicate(families: tuple[str, ...]) -> str:
    """SELECT de descoberta dos beats: escopo nomeado e ação da família, nada mais.

    Ver `app/db/worker_discovery.py`. Sem ator de propósito: não há usuário, há um
    beat; a atribuição é a própria ação `discovery:<família>`.
    """
    actions = " OR ".join(
        f"current_setting('{PLATFORM_ACTION_SETTING}', true) = '{discovery_action(family)}'"
        for family in families
    )
    return (
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = '{WORKER_DISCOVERY_SCOPE}' "
        f"AND ({actions})"
    )


def platform_support_policy_predicate(
    *,
    allow_staff_reassignment: bool = False,
) -> str:
    """Allow tenant access or an attributable SAC/continuity platform context."""
    tenant = workspace_policy_predicate()
    support_actions = " OR ".join(
        f"current_setting('{PLATFORM_ACTION_SETTING}', true) = '{action}'"
        for action in PLATFORM_SUPPORT_DB_ACTIONS
    )
    support = (
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = '{PLATFORM_SUPPORT_SCOPE}' "
        f"AND NULLIF(current_setting('{PLATFORM_ACTOR_SETTING}', true), '') IS NOT NULL "
        f"AND ({support_actions})"
    )
    if not allow_staff_reassignment:
        return f"({tenant}) OR ({support})"

    reassignment_actions = " OR ".join(
        f"current_setting('{PLATFORM_ACTION_SETTING}', true) = '{action}'"
        for action in PLATFORM_STAFF_REASSIGNMENT_DB_ACTIONS
    )
    reassignment = (
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = "
        f"'{PLATFORM_STAFF_REASSIGNMENT_SCOPE}' "
        f"AND NULLIF(current_setting('{PLATFORM_ACTOR_SETTING}', true), '') IS NOT NULL "
        f"AND ({reassignment_actions})"
    )
    return f"({tenant}) OR ({support}) OR ({reassignment})"


def platform_staff_reassignment_policy_predicate() -> str:
    """Fail closed unless an attributable Blackbox continuity context is active."""
    allowed_actions = " OR ".join(
        f"current_setting('{PLATFORM_ACTION_SETTING}', true) = '{action}'"
        for action in PLATFORM_STAFF_REASSIGNMENT_DB_ACTIONS
    )
    return (
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = "
        f"'{PLATFORM_STAFF_REASSIGNMENT_SCOPE}' "
        f"AND NULLIF(current_setting('{PLATFORM_ACTOR_SETTING}', true), '') IS NOT NULL "
        f"AND ({allowed_actions})"
    )


def _platform_control_capability_predicate(*capabilities: tuple[str, str]) -> str:
    """Require an attributable root context or one exact RBAC capability."""

    allowed = " OR ".join(
        "("
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = '{module}' "
        f"AND current_setting('{PLATFORM_ACTION_SETTING}', true) = '{module}:{action}'"
        ")"
        for module, action in capabilities
    )
    return (
        f"NULLIF(current_setting('{PLATFORM_ACTOR_SETTING}', true), '') IS NOT NULL "
        "AND ("
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = 'platform_root'"
        + (f" OR {allowed}" if allowed else "")
        + ")"
    )


_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES: tuple[tuple[str, str], ...] = (
    # These are not aliases for commercial administration.  They are the
    # already-authorized entry points that create or revise the same
    # canonical contract aggregate during prospect conversion, customer
    # provisioning and billing operations.  The command service revalidates
    # the originating capability before installing this database context.
    ("sales", "manage"),
    ("customers", "manage"),
    ("finance", "manage"),
)
_COMMERCIAL_PORTABILITY_REBIND_CAPABILITY = ("commercial_portability", "rebind")
_COMMERCIAL_PORTABILITY_RESTORE_REBIND_CAPABILITY = (
    "commercial_portability",
    "restore_rebind",
)
_COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY = ("billing_provider", "reconcile")


_COMMERCIAL_TABLE_READ_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    # Offer revisions are consumed while previewing/resolving a contract, not
    # only by the Plan Studio itself.
    "commercial_offers": (
        ("plans", "view"),
        ("plans", "manage"),
        ("plans", "draft"),
        ("plans", "preview"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    "commercial_offer_versions": (
        ("plans", "view"),
        ("plans", "manage"),
        ("plans", "draft"),
        ("plans", "preview"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    # Contract reads assemble lines, pricing adjustments and bonuses.  The
    # adjustment service also validates the owning contract and line.
    "workspace_commercial_contracts": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "view"),
        ("commercial_adjustments", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    "workspace_contract_lines": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    "commercial_adjustments": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "view"),
        ("commercial_adjustments", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PORTABILITY_REBIND_CAPABILITY,
        _COMMERCIAL_PORTABILITY_RESTORE_REBIND_CAPABILITY,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    "entitlement_bonuses": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("entitlement_bonuses", "view"),
        ("entitlement_bonuses", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PORTABILITY_REBIND_CAPABILITY,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    # Receipts are read before every idempotent command.  Events and receipts
    # have no tenant policy and are never exposed as raw tenant history.
    "commercial_command_receipts": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    "commercial_catalog_events": (
        ("plans", "view"),
        ("plans", "draft"),
        ("plans", "preview"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
}

_COMMERCIAL_TABLE_INSERT_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    # Offer creation/publication is intentionally root-only.  Passing no
    # normal capability to the helper preserves the attributable actor check.
    "commercial_offers": (),
    "commercial_offer_versions": (),
    "workspace_commercial_contracts": (
        ("commercial_contracts", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    "workspace_contract_lines": (
        ("commercial_contracts", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    # Contract revision never creates a discount or bonus.  Those records
    # remain governed exclusively by their dedicated management capability.
    "commercial_adjustments": (("commercial_adjustments", "manage"),),
    "entitlement_bonuses": (("entitlement_bonuses", "manage"),),
    "commercial_command_receipts": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    "commercial_catalog_events": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
}

_COMMERCIAL_TABLE_UPDATE_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "workspace_commercial_contracts": (
        ("commercial_contracts", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    "workspace_contract_lines": (
        ("commercial_contracts", "manage"),
        *_COMMERCIAL_WORKFLOW_MANAGE_CAPABILITIES,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    # Direct management remains explicit.  Contract revision temporarily
    # installs the non-public internal context only while re-binding the
    # portability state of existing records.
    "commercial_adjustments": (
        ("commercial_adjustments", "manage"),
        _COMMERCIAL_PORTABILITY_REBIND_CAPABILITY,
        _COMMERCIAL_PORTABILITY_RESTORE_REBIND_CAPABILITY,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
    "entitlement_bonuses": (
        ("entitlement_bonuses", "manage"),
        _COMMERCIAL_PORTABILITY_REBIND_CAPABILITY,
        _COMMERCIAL_PROVIDER_RECONCILE_CAPABILITY,
    ),
}


def commercial_tenant_read_policy_predicate(table_name: str) -> str:
    """Return the SELECT-only tenant predicate for commercial manifest data."""

    if table_name not in COMMERCIAL_TENANT_READ_TABLES:
        raise KeyError(f"{table_name} is not tenant-readable commercial data")
    workspace = f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    if table_name == "workspace_commercial_contracts":
        tenant = f"workspace_id::text = {workspace}"
    elif table_name == "workspace_contract_lines":
        tenant = (
            "EXISTS (SELECT 1 FROM workspace_commercial_contracts "
            "WHERE workspace_commercial_contracts.id = "
            "workspace_contract_lines.contract_id "
            f"AND workspace_commercial_contracts.workspace_id::text = {workspace})"
        )
    elif table_name == "commercial_adjustments":
        tenant = (
            "EXISTS (SELECT 1 FROM workspace_commercial_contracts "
            "WHERE workspace_commercial_contracts.id = commercial_adjustments.contract_id "
            f"AND workspace_commercial_contracts.workspace_id::text = {workspace})"
        )
    elif table_name == "entitlement_bonuses":
        tenant = f"workspace_id::text = {workspace}"
    elif table_name == "commercial_offers":
        tenant = (
            "(EXISTS (SELECT 1 FROM commercial_offer_versions "
            "WHERE commercial_offer_versions.offer_id = commercial_offers.id "
            "AND commercial_offer_versions.is_public = true "
            "AND commercial_offer_versions.is_active = true "
            f"AND {workspace} IS NOT NULL)) OR EXISTS ("
            "SELECT 1 FROM workspace_contract_lines "
            "JOIN workspace_commercial_contracts ON "
            "workspace_commercial_contracts.id = workspace_contract_lines.contract_id "
            "WHERE workspace_contract_lines.offer_id = commercial_offers.id "
            f"AND workspace_commercial_contracts.workspace_id::text = {workspace})"
        )
    elif table_name == "commercial_offer_versions":
        tenant = (
            "(is_public = true AND is_active = true "
            f"AND {workspace} IS NOT NULL) OR EXISTS ("
            "SELECT 1 FROM workspace_contract_lines "
            "JOIN workspace_commercial_contracts ON "
            "workspace_commercial_contracts.id = workspace_contract_lines.contract_id "
            "WHERE workspace_contract_lines.policy_version_id = commercial_offer_versions.id "
            f"AND workspace_commercial_contracts.workspace_id::text = {workspace})"
        )
    else:  # pragma: no cover - guarded by COMMERCIAL_TENANT_READ_TABLES
        raise KeyError(f"No tenant commercial predicate for {table_name}")
    return tenant


def commercial_platform_read_policy_predicate(table_name: str) -> str:
    """Require the exact attributable capability needed to read a table."""

    return _platform_control_capability_predicate(
        *_COMMERCIAL_TABLE_READ_CAPABILITIES[table_name]
    )


def commercial_platform_insert_policy_predicate(table_name: str) -> str:
    """Require the exact attributable capability allowed to insert a row."""

    return _platform_control_capability_predicate(
        *_COMMERCIAL_TABLE_INSERT_CAPABILITIES[table_name]
    )


def commercial_platform_update_policy_predicate(table_name: str) -> str:
    """Require direct management or the bounded internal portability context."""

    return _platform_control_capability_predicate(
        *_COMMERCIAL_TABLE_UPDATE_CAPABILITIES[table_name]
    )


def commercial_platform_mutation_policy_predicate(table_name: str) -> str:
    """Deprecated fail-closed compatibility alias.

    INSERT and UPDATE now intentionally have different capabilities.  Generic
    mutation callers must not guess which operation is being authorized.
    """

    del table_name
    return _platform_control_capability_predicate()


def commercial_control_policy_predicate(table_name: str) -> str:
    """Deprecated root-only compatibility alias.

    Commercial tables no longer have one tenant-plus-platform ``FOR ALL``
    policy.  Callers creating PostgreSQL policies must use
    :data:`COMMERCIAL_RLS_POLICY_SPECS`; this alias intentionally fails closed
    for every non-root context.
    """

    return commercial_platform_mutation_policy_predicate(table_name)


def platform_task_control_policy_predicate() -> str:
    """Fail closed outside the attributable internal-task RBAC context."""

    return _platform_control_capability_predicate(
        *(
            ("platform_tasks", action)
            for action in (
                "view",
                "create",
                "edit",
                "assign",
                "review",
                "complete",
                "comment",
            )
        )
    )


_PLATFORM_TASK_TABLE_READ_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "platform_tasks": tuple(
        ("platform_tasks", action)
        for action in ("view", "create", "edit", "assign", "review", "complete", "comment")
    ),
    "platform_task_participants": tuple(
        ("platform_tasks", action)
        for action in ("view", "create", "edit", "assign", "review", "complete", "comment")
    ),
    "platform_task_review_rounds": tuple(
        ("platform_tasks", action)
        for action in ("view", "create", "edit", "assign", "review", "complete", "comment")
    ),
    # Event history is intentionally narrower than task mutation access.
    "platform_task_events": (("platform_tasks", "view"),),
}

_PLATFORM_TASK_TABLE_INSERT_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "platform_tasks": (("platform_tasks", "create"),),
    "platform_task_participants": (
        ("platform_tasks", "create"),
        ("platform_tasks", "assign"),
    ),
    "platform_task_review_rounds": (("platform_tasks", "edit"),),
    "platform_task_events": tuple(
        ("platform_tasks", action)
        for action in ("create", "edit", "assign", "review", "complete", "comment")
    ),
}

_PLATFORM_TASK_TABLE_UPDATE_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "platform_tasks": tuple(
        ("platform_tasks", action)
        for action in ("edit", "assign", "review", "complete")
    ),
    "platform_task_participants": (
        ("platform_tasks", "edit"),
        ("platform_tasks", "assign"),
    ),
    "platform_task_review_rounds": (
        ("platform_tasks", "review"),
        ("platform_tasks", "complete"),
    ),
}


def platform_task_read_policy_predicate(table_name: str) -> str:
    return _platform_control_capability_predicate(
        *_PLATFORM_TASK_TABLE_READ_CAPABILITIES[table_name]
    )


def platform_task_insert_policy_predicate(table_name: str) -> str:
    return _platform_control_capability_predicate(
        *_PLATFORM_TASK_TABLE_INSERT_CAPABILITIES[table_name]
    )


def platform_task_update_policy_predicate(table_name: str) -> str:
    return _platform_control_capability_predicate(
        *_PLATFORM_TASK_TABLE_UPDATE_CAPABILITIES[table_name]
    )


def parent_workspace_policy_predicate(table_name: str) -> str:
    """Return a fail-closed EXISTS predicate for a registered child table."""
    policy = PARENT_WORKSPACE_POLICIES[table_name]
    parent = policy.parent_table
    conditions = [
        f"{parent}.{policy.parent_reference_column} = {table_name}.{policy.child_reference_column}"
    ]
    conditions.extend(
        f"{parent}.{column_name} = {table_name}.{column_name}"
        for column_name in policy.shared_reference_columns
    )
    conditions.append(
        f"{parent}.workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )
    return f"EXISTS (SELECT 1 FROM {parent} WHERE {' AND '.join(conditions)})"


@dataclass(frozen=True)
class RLSPolicySpec:
    """One exact policy row expected in PostgreSQL's ``pg_policies``."""

    name: str
    command: str
    using: str | None
    with_check: str | None
    permissive: str = "PERMISSIVE"
    roles: tuple[str, ...] = ("public",)


RLS_POLICY_PREDICATES = {
    **{table_name: workspace_policy_predicate() for table_name in RLS_DIRECT_TENANT_TABLES},
    **{
        table_name: platform_support_policy_predicate(
            allow_staff_reassignment=table_name == "support_tickets"
        )
        for table_name in SUPPORT_DIRECT_TENANT_TABLES
    },
    **{
        table_name: parent_workspace_policy_predicate(table_name)
        for table_name in RLS_PARENT_TENANT_TABLES
    },
    **{
        table_name: platform_staff_reassignment_policy_predicate()
        for table_name in PLATFORM_STAFF_REASSIGNMENT_TABLES
    },
}

COMMERCIAL_RLS_POLICY_SPECS: dict[str, tuple[RLSPolicySpec, ...]] = {
    **{
        table_name: (
            RLSPolicySpec(
                name=COMMERCIAL_TENANT_READ_POLICY_NAME,
                command="SELECT",
                using=commercial_tenant_read_policy_predicate(table_name),
                with_check=None,
            ),
            RLSPolicySpec(
                name=COMMERCIAL_PLATFORM_READ_POLICY_NAME,
                command="SELECT",
                using=commercial_platform_read_policy_predicate(table_name),
                with_check=None,
            ),
            RLSPolicySpec(
                name=COMMERCIAL_PLATFORM_INSERT_POLICY_NAME,
                command="INSERT",
                using=None,
                with_check=commercial_platform_insert_policy_predicate(table_name),
            ),
            *(
                (
                    RLSPolicySpec(
                        name=COMMERCIAL_PLATFORM_UPDATE_POLICY_NAME,
                        command="UPDATE",
                        using=commercial_platform_update_policy_predicate(table_name),
                        with_check=commercial_platform_update_policy_predicate(table_name),
                    ),
                )
                if table_name
                in {
                    "commercial_adjustments",
                    "entitlement_bonuses",
                    "workspace_commercial_contracts",
                    "workspace_contract_lines",
                }
                else ()
            ),
        )
        for table_name in COMMERCIAL_TENANT_READ_TABLES
    },
    **{
        table_name: (
            RLSPolicySpec(
                name=COMMERCIAL_PLATFORM_READ_POLICY_NAME,
                command="SELECT",
                using=commercial_platform_read_policy_predicate(table_name),
                with_check=None,
            ),
            RLSPolicySpec(
                name=COMMERCIAL_PLATFORM_INSERT_POLICY_NAME,
                command="INSERT",
                using=None,
                with_check=commercial_platform_insert_policy_predicate(table_name),
            ),
        )
        for table_name in COMMERCIAL_PLATFORM_ONLY_TABLES
    },
}

PLATFORM_TASK_RLS_POLICY_SPECS: dict[str, tuple[RLSPolicySpec, ...]] = {
    table_name: (
        RLSPolicySpec(
            name=PLATFORM_TASK_SELECT_POLICY_NAME,
            command="SELECT",
            using=platform_task_read_policy_predicate(table_name),
            with_check=None,
        ),
        RLSPolicySpec(
            name=PLATFORM_TASK_INSERT_POLICY_NAME,
            command="INSERT",
            using=None,
            with_check=platform_task_insert_policy_predicate(table_name),
        ),
        *(
            (
                RLSPolicySpec(
                    name=PLATFORM_TASK_UPDATE_POLICY_NAME,
                    command="UPDATE",
                    using=platform_task_update_policy_predicate(table_name),
                    with_check=platform_task_update_policy_predicate(table_name),
                ),
            )
            if table_name in _PLATFORM_TASK_TABLE_UPDATE_CAPABILITIES
            else ()
        ),
    )
    for table_name in PLATFORM_TASK_CONTROL_TABLES
}

RLS_POLICY_SPECS: dict[str, tuple[RLSPolicySpec, ...]] = {
    **{
        table_name: (
            RLSPolicySpec(
                name=RLS_POLICY_NAME,
                command="ALL",
                using=predicate,
                with_check=predicate,
            ),
        )
        for table_name, predicate in RLS_POLICY_PREDICATES.items()
    },
    **COMMERCIAL_RLS_POLICY_SPECS,
    **PLATFORM_TASK_RLS_POLICY_SPECS,
}
for _resolver_table_name in RESOLVER_OWNER_LOOKUP_TABLES:
    RLS_POLICY_SPECS[_resolver_table_name] = (
        *RLS_POLICY_SPECS[_resolver_table_name],
        RLSPolicySpec(
            name=RESOLVER_OWNER_POLICY_NAME,
            command="SELECT",
            using="true",
            with_check=None,
            roles=(RESOLVER_OWNER_ROLE_MARKER,),
        ),
    )

# Descoberta dos beats: policy SELECT extra por tabela, ligada ao escopo
# `worker_discovery` e à ação da família. Mesmo idioma da policy do dono dos
# resolvers acima; a migration que a instala tem de andar em lockstep com este
# inventário, porque o guard compara os dois a cada conexão.
WORKER_DISCOVERY_TABLE_FAMILIES = worker_discovery_table_families()
for _discovery_table_name, _discovery_families in WORKER_DISCOVERY_TABLE_FAMILIES.items():
    RLS_POLICY_SPECS[_discovery_table_name] = (
        *RLS_POLICY_SPECS[_discovery_table_name],
        RLSPolicySpec(
            name=WORKER_DISCOVERY_POLICY_NAME,
            command="SELECT",
            using=worker_discovery_policy_predicate(_discovery_families),
            with_check=None,
        ),
    )

_TABLE_DML_PRIVILEGES = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})


def expected_runtime_table_privileges(
    table_name: str,
    *,
    immutable: bool,
) -> frozenset[str]:
    """Return the exact DML surface needed by the published RLS policies.

    PostgreSQL table grants are checked independently from RLS.  A policy that
    could authorize an operation is useless without the matching table grant,
    while an unnecessary grant widens the blast radius of policy drift.  An
    immutable evidence table never needs ``UPDATE`` or ``DELETE`` even when an
    older broad ``FOR ALL`` policy still exists; its database trigger is the
    authoritative append-only boundary.
    """

    specs = RLS_POLICY_SPECS[table_name]
    expected: set[str] = set()
    for spec in specs:
        command = spec.command.upper()
        if command == "ALL":
            expected.update(_TABLE_DML_PRIVILEGES)
        elif command in _TABLE_DML_PRIVILEGES:
            expected.add(command)
    if immutable:
        expected.difference_update({"UPDATE", "DELETE"})
    return frozenset(expected)

_QUOTED_TABLE_NAMES = ", ".join(f"'{table}'" for table in RLS_TENANT_TABLES)
_RESOLVER_REGPROCEDURES = ",\n                  ".join(
    f"to_regprocedure('{signature}')" for signature in TENANT_RESOLVER_FUNCTION_SIGNATURES.values()
)


class RLSRuntimeConfigurationError(RuntimeError):
    """Raised when the runtime database role can bypass or lacks required RLS."""


@dataclass(frozen=True)
class RLSRuntimeAudit:
    role_name: str
    protected_tables: tuple[str, ...]


def _fetchone(cursor, statement: str) -> tuple[Any, ...]:
    cursor.execute(statement)
    row = cursor.fetchone()
    if row is None:
        raise RLSRuntimeConfigurationError("Could not inspect the PostgreSQL runtime role.")
    return tuple(row)


def _normalize_catalog_expression(value: str | None) -> str:
    if value is None:
        return ""
    normalized = "".join(value.casefold().split()).replace('"', "")
    normalized = normalized.replace("::text", "")
    return normalized.translate(str.maketrans("", "", "()"))


def policy_expression_matches(table_name: str, value: str | None) -> bool:
    """Compare a deparsed PostgreSQL policy expression with the inventory."""
    expected = RLS_POLICY_PREDICATES[table_name]
    return _normalize_catalog_expression(value) == _normalize_catalog_expression(expected)


def policy_spec_expression_matches(expected: str | None, value: str | None) -> bool:
    """Compare a catalog expression against one multi-policy specification."""

    return _normalize_catalog_expression(value) == _normalize_catalog_expression(expected)


def _normalize_function_body(value: str) -> str:
    return " ".join(value.split()).replace("::text", "").strip("() ")


def _normalize_commercial_portability_trigger_definition(
    value: str,
    *,
    table_name: str,
) -> str:
    """Canonicalize only harmless PostgreSQL trigger deparser differences."""

    normalized = " ".join(value.split()).replace('"', "").rstrip(";").lower()
    normalized = normalized.replace(
        f" on {table_name} ",
        f" on public.{table_name} ",
    )
    normalized = normalized.replace(
        f" execute function {COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME}()",
        " execute function "
        f"public.{COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME}()",
    )
    return normalized


def _expected_commercial_portability_trigger_definition(
    table_name: str,
    trigger_name: str,
) -> str:
    return (
        f"create trigger {trigger_name} before update on public.{table_name} "
        "for each row execute function "
        f"public.{COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME}()"
    )


def _audit_tenant_resolvers(cursor, *, runtime_role: str) -> str:
    """Return the single non-privileged owner after exact resolver validation."""

    cursor.execute(
        f"""
        SELECT procedure.proname,
               owner.rolname,
               owner.rolsuper,
               owner.rolbypassrls,
               owner.rolcreaterole,
               owner.rolcreatedb,
               owner.rolreplication,
               owner.rolinherit,
               owner.rolcanlogin,
               EXISTS (
                   SELECT 1
                   FROM pg_roles AS privileged_owner_role
                   WHERE (
                       privileged_owner_role.rolsuper
                       OR privileged_owner_role.rolbypassrls
                   )
                     AND pg_has_role(owner.oid, privileged_owner_role.oid, 'MEMBER')
               ),
               pg_has_role(current_user, owner.oid, 'MEMBER'),
               has_schema_privilege(owner.rolname, current_schema(), 'USAGE'),
               has_schema_privilege(owner.rolname, current_schema(), 'CREATE'),
               procedure.prosecdef,
               has_function_privilege(current_user, procedure.oid, 'EXECUTE'),
               procedure.proconfig,
               procedure.prosrc,
               language.lanname,
               procedure.provolatile::text,
               procedure.proisstrict,
               ARRAY(
                   SELECT acl_role
                   FROM (
                       SELECT DISTINCT COALESCE(grantee.rolname, 'public') AS acl_role
                       FROM aclexplode(
                           COALESCE(
                               procedure.proacl,
                               acldefault('f', procedure.proowner)
                           )
                       ) AS privilege
                       LEFT JOIN pg_roles AS grantee
                         ON grantee.oid = privilege.grantee
                       WHERE privilege.privilege_type = 'EXECUTE'
                   ) AS explicit_execute_roles
                   ORDER BY acl_role
               )
        FROM pg_proc AS procedure
        JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
        JOIN pg_roles AS owner ON owner.oid = procedure.proowner
        JOIN pg_language AS language ON language.oid = procedure.prolang
        WHERE namespace.nspname = current_schema()
          AND procedure.oid IN (
              {_RESOLVER_REGPROCEDURES}
          )
        """
    )
    resolver_rows = {row[0]: tuple(row[1:]) for row in cursor.fetchall()}
    unsafe_resolvers: list[str] = []
    resolver_owners: set[str] = set()
    for function_name in TENANT_RESOLVER_FUNCTIONS:
        state = resolver_rows.get(function_name)
        if state is None:
            unsafe_resolvers.append(function_name)
            continue
        (
            owner_name,
            owner_superuser,
            owner_bypasses_rls,
            owner_creates_roles,
            owner_creates_databases,
            owner_replicates,
            owner_inherits,
            owner_can_login,
            owner_can_assume_bypass,
            runtime_is_owner_member,
            owner_schema_usage,
            owner_schema_create,
            security_definer,
            runtime_execute,
            config,
            body,
            language_name,
            volatility,
            strict,
            execute_roles,
        ) = state
        resolver_owners.add(str(owner_name))
        expected_execute_roles = {str(owner_name), runtime_role}
        if (
            owner_name == runtime_role
            or owner_superuser
            or owner_bypasses_rls
            or owner_creates_roles
            or owner_creates_databases
            or owner_replicates
            or owner_inherits
            or owner_can_login
            or owner_can_assume_bypass
            or runtime_is_owner_member
            or not owner_schema_usage
            or owner_schema_create
            or not security_definer
            or not runtime_execute
            or set(config or ()) != {"search_path=pg_catalog, pg_temp"}
            or _normalize_function_body(body)
            != _normalize_function_body(TENANT_RESOLVER_FUNCTION_BODIES[function_name])
            or language_name != "sql"
            or volatility != "s"
            or strict != TENANT_RESOLVER_STRICTNESS[function_name]
            or set(execute_roles or ()) != expected_execute_roles
        ):
            unsafe_resolvers.append(function_name)

    if len(resolver_owners) != 1:
        unsafe_resolvers.extend(TENANT_RESOLVER_FUNCTIONS)
    if unsafe_resolvers:
        raise RLSRuntimeConfigurationError(
            "Missing or unsafe tenant-bound resolvers: "
            + ", ".join(sorted(set(unsafe_resolvers)))
        )
    resolver_owner = resolver_owners.pop()
    cursor.execute(
        f"""
        WITH resolver_owner AS (
            SELECT DISTINCT owner.rolname AS role_name
            FROM pg_proc AS procedure
            JOIN pg_roles AS owner ON owner.oid = procedure.proowner
            WHERE procedure.oid IN (
                {_RESOLVER_REGPROCEDURES}
            )
        )
        SELECT relation.relname,
               has_table_privilege(resolver_owner.role_name, relation.oid, 'SELECT'),
               has_table_privilege(resolver_owner.role_name, relation.oid, 'INSERT'),
               has_table_privilege(resolver_owner.role_name, relation.oid, 'UPDATE'),
               has_table_privilege(resolver_owner.role_name, relation.oid, 'DELETE'),
               has_table_privilege(resolver_owner.role_name, relation.oid, 'TRUNCATE'),
               has_table_privilege(resolver_owner.role_name, relation.oid, 'REFERENCES'),
               has_table_privilege(resolver_owner.role_name, relation.oid, 'TRIGGER')
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        CROSS JOIN resolver_owner
        WHERE namespace.nspname = current_schema()
          AND relation.relname IN (
              'matters',
              'whatsapp_connections',
              'signup_verifications',
              'workspace_invitations',
              'identity_outbox_messages',
              'integration_connections'
        )
        ORDER BY relation.relname
        """
    )
    owner_table_privileges = cursor.fetchall()
    if len(owner_table_privileges) != 6 or any(
        tuple(row[1:]) != (True, False, False, False, False, False, False)
        for row in owner_table_privileges
    ):
        raise RLSRuntimeConfigurationError(
            "Dedicated resolver owner has missing or excessive lookup-table privileges."
        )
    return resolver_owner


def _audit_commercial_portability_guard(cursor) -> None:
    """Require the column-bounded trigger behind the internal rebind context."""

    cursor.execute(
        """
        SELECT procedure.prosrc,
               procedure.prosecdef,
               language.lanname,
               procedure.proconfig,
               EXISTS (
                   SELECT 1
                   FROM aclexplode(
                       COALESCE(
                           procedure.proacl,
                           acldefault('f', procedure.proowner)
                       )
                   ) AS privilege
                   WHERE privilege.grantee = 0
                     AND privilege.privilege_type = 'EXECUTE'
               )
        FROM pg_proc AS procedure
        JOIN pg_namespace AS namespace
          ON namespace.oid = procedure.pronamespace
        JOIN pg_language AS language
          ON language.oid = procedure.prolang
        WHERE namespace.nspname = 'public'
          AND procedure.oid = to_regprocedure(
              'public.klm_guard_commercial_portability_update()'
          )
        """
    )
    function_state = cursor.fetchone()
    if function_state is None:
        raise RLSRuntimeConfigurationError(
            "Missing commercial portability update guard function."
        )
    body, security_definer, language, config, public_execute = function_state
    if (
        _normalize_function_body(str(body))
        != _normalize_function_body(COMMERCIAL_PORTABILITY_GUARD_FUNCTION_BODY)
        or bool(security_definer)
        or str(language) != "plpgsql"
        or set(config or ()) != {"search_path=pg_catalog, pg_temp"}
        or bool(public_execute)
    ):
        raise RLSRuntimeConfigurationError(
            "Commercial portability update guard function is unsafe or drifted."
        )

    cursor.execute(
        """
        SELECT relation.relname,
               trigger.tgname,
               procedure.proname,
               function_namespace.nspname,
               trigger.tgenabled::text,
               trigger.tgtype,
               trigger.tgqual IS NULL,
               trigger.tgattr::text,
               trigger.tgnargs,
               trigger.tgfoid = to_regprocedure(
                   'public.klm_guard_commercial_portability_update()'
               ),
               pg_get_triggerdef(trigger.oid)
        FROM pg_trigger AS trigger
        JOIN pg_class AS relation
          ON relation.oid = trigger.tgrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        JOIN pg_proc AS procedure
          ON procedure.oid = trigger.tgfoid
        JOIN pg_namespace AS function_namespace
          ON function_namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = 'public'
          AND trigger.tgname IN (
              'trg_commercial_adjustments_portability_guard',
              'trg_entitlement_bonuses_portability_guard'
          )
          AND NOT trigger.tgisinternal
        """
    )
    trigger_rows = cursor.fetchall()
    expected = {
        (table_name, trigger_name)
        for table_name, trigger_name in COMMERCIAL_PORTABILITY_GUARD_TRIGGERS.items()
    }
    actual = {(str(row[0]), str(row[1])) for row in trigger_rows}
    if actual != expected:
        raise RLSRuntimeConfigurationError(
            "Commercial portability update guard triggers are missing or drifted."
        )
    for (
        table_name,
        trigger_name,
        function_name,
        function_namespace,
        enabled,
        trigger_type,
        no_when_clause,
        update_columns,
        argument_count,
        exact_function,
        trigger_definition,
    ) in trigger_rows:
        normalized_definition = (
            _normalize_commercial_portability_trigger_definition(
                str(trigger_definition),
                table_name=str(table_name),
            )
        )
        expected_definition = (
            _expected_commercial_portability_trigger_definition(
                str(table_name),
                str(trigger_name),
            )
        )
        trigger_invariants = (
            (
                "function_name",
                str(function_name),
                COMMERCIAL_PORTABILITY_GUARD_FUNCTION_NAME,
            ),
            ("function_namespace", str(function_namespace), "public"),
            ("enabled", str(enabled), "O"),
            # PostgreSQL trigger bitmask: ROW(1) | BEFORE(2) | UPDATE(16).
            ("trigger_type", int(trigger_type), 19),
            ("no_when_clause", bool(no_when_clause), True),
            ("update_columns", str(update_columns), ""),
            ("argument_count", int(argument_count), 0),
            ("exact_function", bool(exact_function), True),
            ("definition", normalized_definition, expected_definition),
        )
        drift: list[str] = []
        for name, actual_value, expected_value in trigger_invariants:
            if actual_value == expected_value:
                continue
            if name == "definition":
                actual_value = (
                    "sha256:"
                    + sha256(str(actual_value).encode("utf-8")).hexdigest()
                )
                expected_value = (
                    "sha256:"
                    + sha256(str(expected_value).encode("utf-8")).hexdigest()
                )
            drift.append(
                f"{name}: expected={expected_value!r}, actual={actual_value!r}"
            )
        if drift:
            raise RLSRuntimeConfigurationError(
                "Commercial portability update guard trigger is unsafe or drifted "
                f"for public.{table_name}/{trigger_name}: "
                + "; ".join(drift)
            )


def audit_postgresql_runtime_connection(dbapi_connection) -> RLSRuntimeAudit:
    """Verify role attributes, ownership, FORCE RLS, and the expected policies."""
    cursor = dbapi_connection.cursor()
    try:
        (
            role_name,
            is_superuser,
            bypasses_rls,
            creates_roles,
            creates_databases,
            replicates,
            can_assume_bypass,
            has_schema_usage,
            has_schema_create,
        ) = _fetchone(
            cursor,
            """
            SELECT current_user,
                   runtime_role.rolsuper,
                   runtime_role.rolbypassrls,
                   runtime_role.rolcreaterole,
                   runtime_role.rolcreatedb,
                   runtime_role.rolreplication,
                   EXISTS (
                       SELECT 1
                       FROM pg_roles privileged_role
                       WHERE (privileged_role.rolsuper OR privileged_role.rolbypassrls)
                         AND pg_has_role(current_user, privileged_role.oid, 'MEMBER')
                   ),
                   has_schema_privilege(current_user, current_schema(), 'USAGE'),
                   has_schema_privilege(current_user, current_schema(), 'CREATE')
            FROM pg_roles runtime_role
            WHERE runtime_role.rolname = current_user
            """,
        )
        if (
            is_superuser
            or bypasses_rls
            or creates_roles
            or creates_databases
            or replicates
            or can_assume_bypass
            or not has_schema_usage
            or has_schema_create
        ):
            raise RLSRuntimeConfigurationError(
                f'PostgreSQL runtime role "{role_name}" has unsafe database privileges.'
            )

        resolver_owner = _audit_tenant_resolvers(cursor, runtime_role=str(role_name))
        _audit_commercial_portability_guard(cursor)

        cursor.execute(
            f"""
            SELECT relation.relname,
                   relation.relrowsecurity,
                   relation.relforcerowsecurity,
                   pg_get_userbyid(relation.relowner),
                   pg_has_role(current_user, relation.relowner, 'MEMBER'),
                   has_table_privilege(current_user, relation.oid, 'SELECT'),
                   has_table_privilege(current_user, relation.oid, 'INSERT'),
                   has_table_privilege(current_user, relation.oid, 'UPDATE'),
                   has_table_privilege(current_user, relation.oid, 'DELETE'),
                   EXISTS (
                       SELECT 1
                       FROM pg_trigger immutable_trigger
                       WHERE immutable_trigger.tgrelid = relation.oid
                         AND NOT immutable_trigger.tgisinternal
                         AND immutable_trigger.tgname LIKE '%%immutable%%'
                   )
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relkind IN ('r', 'p')
              AND relation.relname IN ({_QUOTED_TABLE_NAMES})
            ORDER BY relation.relname
            """
        )
        table_rows = {row[0]: tuple(row[1:]) for row in cursor.fetchall()}
        missing_tables = sorted(set(RLS_TENANT_TABLES) - set(table_rows))
        if missing_tables:
            raise RLSRuntimeConfigurationError(
                "RLS migration is missing protected tables: " + ", ".join(missing_tables)
            )

        unsafe_tables: list[str] = []
        owned_tables: list[str] = []
        missing_dml: list[str] = []
        excessive_dml: list[str] = []
        for table_name, table_state in table_rows.items():
            (
                enabled,
                forced,
                owner_name,
                can_assume_owner,
                can_select,
                can_insert,
                can_update,
                can_delete,
                immutable,
            ) = table_state
            if not enabled or not forced:
                unsafe_tables.append(table_name)
            if can_assume_owner:
                owned_tables.append(f"{table_name} (owner: {owner_name})")
            actual_privileges = {
                privilege
                for privilege, granted in (
                    ("SELECT", can_select),
                    ("INSERT", can_insert),
                    ("UPDATE", can_update),
                    ("DELETE", can_delete),
                )
                if granted
            }
            expected_privileges = expected_runtime_table_privileges(
                table_name,
                immutable=bool(immutable),
            )
            missing = sorted(expected_privileges - actual_privileges)
            excessive = sorted(actual_privileges - expected_privileges)
            if missing:
                missing_dml.append(f"{table_name} ({', '.join(missing)})")
            if excessive:
                excessive_dml.append(f"{table_name} ({', '.join(excessive)})")
        if unsafe_tables:
            raise RLSRuntimeConfigurationError(
                "RLS must be enabled and forced for: " + ", ".join(sorted(unsafe_tables))
            )
        if owned_tables:
            raise RLSRuntimeConfigurationError(
                "The runtime role can assume ownership of protected tables: "
                + ", ".join(sorted(owned_tables))
            )
        if missing_dml:
            raise RLSRuntimeConfigurationError(
                "The runtime role lacks required DML privileges for: "
                + ", ".join(sorted(missing_dml))
            )
        if excessive_dml:
            raise RLSRuntimeConfigurationError(
                "The runtime role has unnecessary DML privileges for: "
                + ", ".join(sorted(excessive_dml))
            )

        cursor.execute(
            f"""
            SELECT tablename, policyname, permissive, cmd, roles, qual, with_check
            FROM pg_policies
            WHERE schemaname = current_schema()
              AND tablename IN ({_QUOTED_TABLE_NAMES})
            """
        )
        policy_rows: dict[str, list[tuple[Any, ...]]] = {}
        for row in cursor.fetchall():
            policy_rows.setdefault(row[0], []).append(tuple(row[1:]))

        invalid_policies: list[str] = []
        for table_name in RLS_TENANT_TABLES:
            table_policies = policy_rows.get(table_name, [])
            expected_specs = RLS_POLICY_SPECS[table_name]
            if len(table_policies) != len(expected_specs):
                invalid_policies.append(table_name)
                continue
            policies_by_name = {row[0]: row for row in table_policies}
            if len(policies_by_name) != len(table_policies):
                invalid_policies.append(table_name)
                continue
            for expected in expected_specs:
                actual = policies_by_name.get(expected.name)
                if actual is None:
                    invalid_policies.append(table_name)
                    break
                (
                    policy_name,
                    permissive,
                    command,
                    roles,
                    using_clause,
                    check_clause,
                ) = actual
                expected_roles = {
                    resolver_owner if role == RESOLVER_OWNER_ROLE_MARKER else role
                    for role in expected.roles
                }
                if (
                    policy_name != expected.name
                    or permissive != expected.permissive
                    or command != expected.command
                    or set(roles or ()) != expected_roles
                    or not policy_spec_expression_matches(expected.using, using_clause)
                    or not policy_spec_expression_matches(expected.with_check, check_clause)
                ):
                    invalid_policies.append(table_name)
                    break
        if invalid_policies:
            raise RLSRuntimeConfigurationError(
                "Missing or incomplete workspace RLS policies for: " + ", ".join(invalid_policies)
            )

        return RLSRuntimeAudit(
            role_name=str(role_name),
            protected_tables=tuple(sorted(table_rows)),
        )
    finally:
        cursor.close()


def install_rls_runtime_guard(engine: AsyncEngine) -> None:
    """Reject unsafe PostgreSQL roles when each physical runtime connection opens."""
    if engine.dialect.name != "postgresql":
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _guard_runtime_connection(dbapi_connection, _connection_record) -> None:
        audit_postgresql_runtime_connection(dbapi_connection)
