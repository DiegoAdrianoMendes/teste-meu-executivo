import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
finally:
    sys.path = original_path


def test_migration_graph_has_single_head() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["c9d0e1f2a3b4"]
    platform_ai_provider_rows = script.get_revision("c9d0e1f2a3b4")
    assert platform_ai_provider_rows.down_revision == "b8d2f4a6c9e1"
    asaas_resolver = script.get_revision("b8d2f4a6c9e1")
    assert asaas_resolver.down_revision == "e3f1a7c9d2b5"
    token_expiration = script.get_revision("zc0v3s6t7p19")
    assert token_expiration.down_revision == "zb9u2r5s6n08"
    client_contract_rls = script.get_revision("zb9u2r5s6n08")
    assert client_contract_rls.down_revision == "za8t1q4r5m97"
    task_organization = script.get_revision("za8t1q4r5m97")
    assert task_organization.down_revision == "yz7s0p4q5l86"
    client_contracts = script.get_revision("yz7s0p4q5l86")
    assert client_contracts.down_revision == "xy6r9n2p3k75"
    task_flow_runs = script.get_revision("xy6r9n2p3k75")
    assert task_flow_runs.down_revision == "wx5q8m1n2j64"
    task_external_shares = script.get_revision("wx5q8m1n2j64")
    assert task_external_shares.down_revision == "uv4p7l9m0h53"
    flexible_task_context = script.get_revision("uv4p7l9m0h53")
    assert flexible_task_context.down_revision == "tv3o6k8l9g42"
    auth_rate_limit_scope_width = script.get_revision("e8c0a2d4f617")
    assert auth_rate_limit_scope_width.down_revision == "e7b9d1f3a526"
    contact_stock = script.get_revision("e7b9d1f3a526")
    assert contact_stock.down_revision == "d6a8c0e2f415"
    commercial_workflow_rls = script.get_revision("d6a8c0e2f415")
    assert commercial_workflow_rls.down_revision == "c5f7a9b1d304"
    webhook_reliability = script.get_revision("c5f7a9b1d304")
    assert webhook_reliability.down_revision == "b4e6f8a0c253"
    owner_exclusivity = script.get_revision("b4e6f8a0c253")
    assert owner_exclusivity.down_revision == "a3d5e7f9b142"
    template_resolver = script.get_revision("a3d5e7f9b142")
    assert template_resolver.down_revision == "f2c4d6e8a031"
    resolver_owner_hardening = script.get_revision("f2c4d6e8a031")
    assert resolver_owner_hardening.down_revision == "e1b3c5d7e920"
    whatsapp_payload_guard = script.get_revision("e1b3c5d7e920")
    assert whatsapp_payload_guard.down_revision == "e0a2b4d6c819"
    task_owner_completion = script.get_revision("e0a2b4d6c819")
    assert task_owner_completion.down_revision == "d9f1a3c5b617"
    official_whatsapp_delivery = script.get_revision("d9f1a3c5b617")
    assert official_whatsapp_delivery.down_revision == "c8e0f2a4b615"
    collaboration_and_commercial = script.get_revision("c8e0f2a4b615")
    assert collaboration_and_commercial.down_revision == "b71d0e4c9f52"
    # WhatsApp 2026 — o telefone deixou de ser a identidade. BSUID (`user_id` no
    # webhook, chegando desde abril/2026) e username convivem com ele, e o
    # `wa_id` é OPCIONAL quando o usuário adota username. Índices únicos viram
    # parciais e a supressão passa a aceitar telefone OU BSUID: sem isso, quem
    # oculta o número pede descadastro e não há onde registrar — LGPD.
    whatsapp_2026 = script.get_revision("b71d0e4c9f52")
    assert whatsapp_2026.down_revision == "f2ab4c6d8e30"
    # Dormente #9 — estado por task individual do Celery Beat:
    # `periodic_task_runs` guarda a última execução (início, fim,
    # sucesso/erro, tipo e mensagem truncada) de cada task do
    # beat_schedule. Tabela de infraestrutura GLOBAL, sem workspace_id e
    # portanto fora do inventário RLS, igual a system_status_observations.
    periodic_task_runs = script.get_revision("a1c4e8b2f760")
    assert periodic_task_runs.down_revision == "f3a7c92e5d81"
    # Sprint B — agenda unificada: `natureza`/`event_type` em `meetings`
    # para misturar reunião comercial e evento jurídico (audiência,
    # sustentação, prazo interno, revisão) na mesma leitura de agenda.
    agenda_unificada = script.get_revision("f3a7c92e5d81")
    assert agenda_unificada.down_revision == "d7e9f1a3c5b6"
    # Decisão do dono (exceção -> task): tasks.source_exception_id <->
    # legal_exceptions.linked_task_id, referência cruzada 1:1 usada por
    # ExceptionCenterService para materializar cada exceção como item
    # humano na mesma lista de tarefas do caso.
    exception_to_task_link = script.get_revision("d7e9f1a3c5b6")
    assert exception_to_task_link.down_revision == "c67346a8bba6"
    # Correção do gate native_architecture (tenant_reference_integrity):
    # klm_legal_artifact_versions.legal_demand_id vira FK composta
    # [workspace_id, legal_demand_id] -> [legal_demands.workspace_id,
    # legal_demands.id], espelhando o padrão já usado por matter_id.
    legal_demand_tenant_fk = script.get_revision("c67346a8bba6")
    assert legal_demand_tenant_fk.down_revision == "46cd1d7279bf"
    # Onda 8 — Operação Massificada / Lotes (Plano Mestre §7): legal_batches
    # (agrupado por critério) + legal_batch_items (estado individual por
    # item, nunca escondido pelo agregado do lote; aprovar tudo bloqueado
    # para tese/prazo_fatal/texto_final/protocolo).
    legal_batch = script.get_revision("46cd1d7279bf")
    assert legal_batch.down_revision == "b6b46ea00c28"
    # Onda 7 — Pasta de protocolo + manifesto (Plano Mestre §10):
    # protocol_folders — projeção mutável que amarra Caso 360 + artefato
    # aprovado numa pasta numerada, com manifesto de conformidade e
    # registro POSTERIOR do recibo (sempre humano).
    protocol_folder = script.get_revision("b6b46ea00c28")
    assert protocol_folder.down_revision == "4ce67f6149cd"
    # Onda 7 — DMS jurídico (Plano Mestre §11): document_assets (canônico
    # deduplicado por hash) + document_asset_references (cada ingestão) +
    # document_share_links/document_share_downloads (compartilhamento com
    # expiração + trilha), mais o novo valor de ExceptionKind para conflito
    # de sincronização com espelho externo.
    legal_dms = script.get_revision("4ce67f6149cd")
    assert legal_dms.down_revision == "a3f9c1e6b204"
    # Onda 6 — Produção jurídica: verificação de citação (súmula/tema/REsp/
    # lei) + achados da revisão adversarial sobre uma LegalArtifactVersion.
    citation_verification = script.get_revision("a3f9c1e6b204")
    assert citation_verification.down_revision == "d0f2a4c6b813"
    # Onda 6 — Editor jurídico: colunas de proveniência/consulta em
    # klm_legal_artifact_versions (legal_demand_id, autor humano/agente,
    # office_template pinado, estado de protocolabilidade).
    legal_editor_provenance = script.get_revision("d0f2a4c6b813")
    assert legal_editor_provenance.down_revision == "e7a9c1d3f5b7"
    # Onda 5 — Caso 360 canônico: CaseParty (polo/papel/qualificação),
    # ligada a uma LegalDemand.
    case_parties = script.get_revision("e7a9c1d3f5b7")
    assert case_parties.down_revision == "cb28b2376b5d"
    # Onda 5 — Matriz Estratégia Fato–Prova–Pedido (Plano Mestre §3 item 8):
    # CaseFact/CaseProofLink/CasePleading, ligados a uma LegalDemand.
    case_strategy = script.get_revision("cb28b2376b5d")
    assert case_strategy.down_revision == "c2d4f6a8e0b3"
    # Onda 4 — Controladoria jurídica: distribuição configurável
    # (DistributionRule/DistributionDecision) + SLA em camadas
    # (SlaEscalationLog + colunas novas em legal_demands).
    legal_controladoria = script.get_revision("c2d4f6a8e0b3")
    assert legal_controladoria.down_revision == "d3f7a9c1b2e4"
    # Onda 4 — jornada publicação→pasta (19 estados, Plano Mestre §6):
    # `journey_state`/`journey_updated_at` em `process_publications`.
    journey_state = script.get_revision("d3f7a9c1b2e4")
    assert journey_state.down_revision == "a04b6e6f22b4"
    # Onda 3 — Importador Universal de Migração (Plano Mestre §12): metade
    # agnóstica (MigrationJob/MigrationStagingRecord/MigrationDivergence).
    migration_jobs = script.get_revision("a04b6e6f22b4")
    assert migration_jobs.down_revision == "3040c77e8efa"
    # Central de Exceções (Plano Mestre §6/§3) — generaliza a fila de
    # aprovações para todo tipo de exceção humana nomeada no plano.
    exception_center = script.get_revision("3040c77e8efa")
    assert exception_center.down_revision == "838f9f719531"
    # Plano Mestre §14 — autonomia A0-A4 por evento (agent_autonomy_policies).
    autonomy_policies = script.get_revision("838f9f719531")
    assert autonomy_policies.down_revision == "013d26e9d488"
    # Onda 2 — Fundação jurídica: modelo canônico "Demanda jurídica" (Plano
    # Mestre §5), unifica as 7 naturezas sem duplicar Matter/Finance/Contrato.
    legal_demands = script.get_revision("013d26e9d488")
    assert legal_demands.down_revision == "58f5e2e75303"
    # Onda 1 item 16 — feedback (thumbs) dos artigos de Conhecimento.
    knowledge_feedback = script.get_revision("58f5e2e75303")
    assert knowledge_feedback.down_revision == "45913a19da62"
    # Onda 1 item 2 — disparo proativo da fila de follow-up.
    follow_up_last_notified = script.get_revision("45913a19da62")
    assert follow_up_last_notified.down_revision == "c7e9a1b3d526"
    executive_council = script.get_revision("c7e9a1b3d526")
    assert executive_council.down_revision == "b6d8f0a2c415"
    # Módulo processual contratável à parte — monitoramento DJEN/DataJud.
    process_monitoring = script.get_revision("b6d8f0a2c415")
    assert process_monitoring.down_revision == "a1f3b5c7d9e2"
    # Overrides de entitlement por conta (Blackbox liga/desliga função avulsa).
    entitlement_overrides = script.get_revision("a1f3b5c7d9e2")
    assert entitlement_overrides.down_revision == "8d3ee1871532"
    # Programa SaaS de indicacoes, beneficios, Partner e Studio de Cupons.
    saas_benefits = script.get_revision("8d3ee1871532")
    assert saas_benefits.down_revision == "7c2cc0760421"
    # F14 — harmonização de documentos financeiros × lançamentos manuais.
    finance_harmonization = script.get_revision("7c2cc0760421")
    assert finance_harmonization.down_revision == "2fbd909dec50"
    # F15 — cópia da conversa por e-mail ao encerrar chamados de suporte.
    support_transcript_email = script.get_revision("2fbd909dec50")
    assert support_transcript_email.down_revision == "44cc21307259"
    # Harmonização multi-área: posse de conversa por agente (CAS) em
    # klm_omnichannel_threads.
    conversation_ownership = script.get_revision("44cc21307259")
    assert conversation_ownership.down_revision == "0c5a0c7328ab"
    # Frente B — catálogo de integrações: expande integration_provider_enum.
    integrations_catalog_expansion = script.get_revision("0c5a0c7328ab")
    assert integrations_catalog_expansion.down_revision == "5914cb063c3b"
    # Rebrand KLM -> Meu Executivo: server_default de author_label.
    rebrand_author_label = script.get_revision("5914cb063c3b")
    assert rebrand_author_label.down_revision == "0d2e2d15c0a9"
    # F11 — contrato → nós financeiros com aprovação: contract_billing_proposals.
    contract_billing = script.get_revision("0d2e2d15c0a9")
    assert contract_billing.down_revision == "8ecdc71ea65b"
    # F9 — honorários jurídicos: fee_arrangements + time_entries.
    fee_arrangements = script.get_revision("8ecdc71ea65b")
    assert fee_arrangements.down_revision == "4d4cb0b6d235"
    # Merge da Onda 2 financeira: dunning colapsa o fork F7×F13 em head único.
    dunning_merge = script.get_revision("4d4cb0b6d235")
    assert set(dunning_merge.down_revision) == {"7d525e80f1c2", "ea59e9f09bf6"}
    bank_reconciliation = script.get_revision("7d525e80f1c2")
    assert bank_reconciliation.down_revision == "601bf15e6501"
    site_leads = script.get_revision("601bf15e6501")
    assert site_leads.down_revision == "f8a2d443a6ee"
    referrals = script.get_revision("ea59e9f09bf6")
    assert referrals.down_revision == "f8a2d443a6ee"
    finance_expense_columns = script.get_revision("f8a2d443a6ee")
    assert finance_expense_columns.down_revision == "705c81985819"
    finance_recurrences = script.get_revision("705c81985819")
    assert finance_recurrences.down_revision == "e2c0c3a7aa8a"
    finance_charges = script.get_revision("e2c0c3a7aa8a")
    assert finance_charges.down_revision == "2f4a6c8e0b31"
    staff_reassignment = script.get_revision("2f4a6c8e0b31")
    assert staff_reassignment.down_revision == "1e3f5a7c9b20"
    identity_surfaces = script.get_revision("1e3f5a7c9b20")
    assert identity_surfaces.down_revision == "0d2f4a6c8e10"
    campaign_quota = script.get_revision("0d2f4a6c8e10")
    assert campaign_quota.down_revision == "f3a5c7d9e1b4"
    support_rls_hardening = script.get_revision("f3a5c7d9e1b4")
    assert support_rls_hardening.down_revision == "e2f4a6b8c0d3"
    support_center = script.get_revision("e2f4a6b8c0d3")
    assert support_center.down_revision == "d1e3f5a7b9c2"
    ai_gateway_events = script.get_revision("d1e3f5a7b9c2")
    assert ai_gateway_events.down_revision == "c0d2e4f6a8b1"
    ai_model_routes = script.get_revision("c0d2e4f6a8b1")
    assert ai_model_routes.down_revision == "b9c1d3e5f7a2"
    owner_whatsapp_phone = script.get_revision("b9c1d3e5f7a2")
    assert owner_whatsapp_phone.down_revision == "b7d1f3a9c520"
    workspace_model_credentials = script.get_revision("b7d1f3a9c520")
    assert workspace_model_credentials.down_revision == "8af3d982fb78"
    growth_crm_prospects = script.get_revision("8af3d982fb78")
    assert growth_crm_prospects.down_revision == "c2f8a1d4e6b9"
    medication_default = script.get_revision("c2f8a1d4e6b9")
    assert medication_default.down_revision == "b1479f28e98b"
    workspace_subscriptions = script.get_revision("b1479f28e98b")
    assert workspace_subscriptions.down_revision == "b5d7f9a1c326"
    meta_hardening_fields = script.get_revision("b5d7f9a1c326")
    assert meta_hardening_fields.down_revision == "a4c6e8f0b215"
    connection_tiers = script.get_revision("a4c6e8f0b215")
    assert connection_tiers.down_revision == "0b7c9e1a2d34"
    native_collaboration = script.get_revision("c6e8a0b2d403")
    assert native_collaboration.down_revision == "b4d6f8a0c201"
    message_evidence = script.get_revision("b4d6f8a0c201")
    assert message_evidence.down_revision == "a2c4e6f8b10c"
    media_reconciliation = script.get_revision("a2c4e6f8b10c")
    assert media_reconciliation.down_revision == "9d1f3a5c7b09"
    broadcast_lists = script.get_revision("4e6a8c0d2f54")
    assert broadcast_lists.down_revision == "3d5f7a9b1e43"
    task_idempotency = script.get_revision("3d5f7a9b1e43")
    assert task_idempotency.down_revision == "2c4e6f8a0d32"
    omnichannel_orchestration = script.get_revision("f9d1e3a5c720")
    assert omnichannel_orchestration.down_revision == "f8c0e2a4b619"
    approval_workflow = script.get_revision("f6b8d0e2a417")
    assert approval_workflow.down_revision == "e5a7c9d1f306"
    operational_rls = script.get_revision("e5a7c9d1f306")
    assert operational_rls.down_revision == "d4f6a8c0e215"
    evidence_integrity = script.get_revision("d4f6a8c0e215")
    assert evidence_integrity.down_revision == "c9e2f4a6b803"
    native_capability_records = script.get_revision("c9e2f4a6b803")
    assert native_capability_records.down_revision == "b8d1e3f5a702"
    dual_access_packages = script.get_revision("b8d1e3f5a702")
    assert dual_access_packages.down_revision == "a7c9e1f3b501"
    whatsapp_outbound_idempotency = script.get_revision("a7c9e1f3b501")
    assert whatsapp_outbound_idempotency.down_revision == "f4a6c8e0b209"
    hybrid_rag = script.get_revision("f4a6c8e0b209")
    assert hybrid_rag.down_revision == "e0b1c2d3f408"
    function_unit_runtime = script.get_revision("e0b1c2d3f408")
    assert function_unit_runtime.down_revision == "d9a7e5c3b102"
    playbook_studio = script.get_revision("d9a7e5c3b102")
    assert playbook_studio.down_revision == "c8f5d2a9b714"
    identity_security = script.get_revision("c8f5d2a9b714")
    assert identity_security.down_revision == "b7e4c1d9a603"
    global_storage_versions = script.get_revision("b7e4c1d9a603")
    assert global_storage_versions.down_revision == "a9d2e4f6b508"
    saas_p1_integrity = script.get_revision("a9d2e4f6b508")
    assert saas_p1_integrity.down_revision == "f8c1d2e3a407"
    identity_lifecycle = script.get_revision("f8c1d2e3a407")
    assert identity_lifecycle.down_revision == "e7a9b1c3d206"
    period_end_cancellation = script.get_revision("e7a9b1c3d206")
    assert period_end_cancellation.down_revision == "e7a9c1d3f205"
    support_access = script.get_revision("e7a9c1d3f205")
    assert support_access.down_revision == "d6f8a0c2e105"
    auth_hardening = script.get_revision("d6f8a0c2e105")
    assert auth_hardening.down_revision == "d5f7a9c1e004"
    local_storage = script.get_revision("d5f7a9c1e004")
    assert local_storage.down_revision == "c4e6f8a0d003"
    entitlements = script.get_revision("c4e6f8a0d003")
    assert entitlements.down_revision == "b3d5f7a9c002"
    subscription_access = script.get_revision("b3d5f7a9c002")
    assert subscription_access.down_revision == "a2c4e6f8b001"
    versioned_configuration = script.get_revision("a2c4e6f8b001")
    assert versioned_configuration.down_revision == "99d0e1f2a3b4"
    legal_artifacts = script.get_revision("98c9d0e1f2a3")
    assert legal_artifacts.down_revision == "97b8c9d0e1f2"
    financial_premises = script.get_revision("97b8c9d0e1f2")
    assert financial_premises.down_revision == "96a7b8c9d0e1"
    chattermate_binding = script.get_revision("96a7b8c9d0e1")
    assert chattermate_binding.down_revision == "95f6a7b8c9d0"
    native_oss = script.get_revision("95f6a7b8c9d0")
    assert native_oss.down_revision == "94e5f6a7b8c9"
    medication_catalog = script.get_revision("94e5f6a7b8c9")
    assert medication_catalog.down_revision == "93d4e5f6a7b8"
    rocketchat_binding = script.get_revision("93d4e5f6a7b8")
    assert rocketchat_binding.down_revision == "92c3d4e5f6a7"
    bottlecrm_binding = script.get_revision("92c3d4e5f6a7")
    assert bottlecrm_binding.down_revision == "91b2c3d4e5f6"
    booking_links = script.get_revision("91b2c3d4e5f6")
    assert booking_links.down_revision == "90a1b2c3d4e5"
    meeting_no_show = script.get_revision("90a1b2c3d4e5")
    assert meeting_no_show.down_revision == "8f92a3b4c5d6"
    public_api_log_rls = script.get_revision("8f92a3b4c5d6")
    assert public_api_log_rls.down_revision == "7f8192a3b4c5"
    email_receipts = script.get_revision("7f8192a3b4c5")
    assert email_receipts.down_revision == "6f708192a3b4"
    inbound_lease = script.get_revision("6f708192a3b4")
    assert inbound_lease.down_revision == "5e6f708192a3"
    inbound_pipeline = script.get_revision("5e6f708192a3")
    assert inbound_pipeline.down_revision == "4d5e6f708192"
    head = script.get_revision("4d5e6f708192")
    assert head.down_revision == "3c4d5e6f7081"
    agent_run_dedupe = script.get_revision("3c4d5e6f7081")
    assert agent_run_dedupe.down_revision == "2b3c4d5e6f70"
    webhook_resolvers = script.get_revision("2b3c4d5e6f70")
    assert webhook_resolvers.down_revision == "2f4a6c8e0b1d"
    scheduled_delivery_leases = script.get_revision("2f4a6c8e0b1d")
    assert scheduled_delivery_leases.down_revision == "1a2b3c4d5e6f"
    flow_runtime_rls = script.get_revision("1a2b3c4d5e6f")
    assert flow_runtime_rls.down_revision == "0f4e5d6c7b8a"
    override_constraint = script.get_revision("0f4e5d6c7b8a")
    assert override_constraint.down_revision == "f3a4b5c6d7e8"
    chatwoot_binding = script.get_revision("f3a4b5c6d7e8")
    assert chatwoot_binding.down_revision == "e2f3a4b5c6d7"
    merge = script.get_revision("e2f3a4b5c6d7")
    assert set(merge.down_revision) == {"c2d3e4f5a6b7", "d1e2f3a4b5c6"}


def test_identity_surface_migration_blocks_both_conflict_directions() -> None:
    source = (
        PROJECT_ROOT
        / "alembic"
        / "versions"
        / "1e3f5a7c9b20_enforce_disjoint_identity_surfaces.py"
    ).read_text(encoding="utf-8")

    assert "trg_workspace_membership_reject_platform_identity" in source
    assert "trg_user_reject_dual_surface_identity" in source
    assert "Identidade Blackbox não pode possuir membership" in source
    assert "Usuário de workspace não pode ser convertido" in source
