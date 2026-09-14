# ONDA 1 — MATRIZ DA VERDADE (2026-07)

> Consolidação read-only dos 4 inventários da Onda 1 (Comercial, Financeiro, Jurídico-Agentes, Plataforma). Nenhum código foi alterado — este documento só organiza o que já foi levantado.
>
> Legenda de estado: **operacional** (cadeia completa UI→API→service→tabela/worker, com RBAC e teste real) · **parcial** (existe mas com elo faltante — RBAC genérico, autor não persistido, sem tela de gestão pós-criação etc.) · **órfão** (backend pronto e testado, sem nenhuma chamada no frontend) · **ausente** (prometido no plano ou esperado, sem código em nenhuma camada) · **simulado** (código real mas roda contra fixture/gate fechado por padrão, sem aviso claro na UI).

## Sumários por área

- **Comercial** — Núcleo (funil, lead/matter, conversas, agenda + booking público, propostas) é OPERACIONAL com RBAC e testes reais. Achados: SDR órfão no frontend; follow-ups sem worker de disparo; edição financeira usa permissão genérica de kanban; Contratos reaproveita módulo genérico; reunião sem link de vídeo automático.
- **Financeiro** — Módulo mais maduro do produto: RBAC real em 11 routers, tabelas próprias, 2 workers agendados, boa cobertura de teste. Achados: aprovações F9/F11 que geram dinheiro gravam approver=NULL; Stripe checkout do workspace é órfão (Local Billing/Asaas é o caminho real); gestão de recorrências e conciliação Via B/lote sem UI; export F10 e /pagar ausentes; 16 arquivos duplicados em benefícios/cupons.
- **Jurídico-Agentes** — Arquitetura fail-closed consistente: capacidades externas passam por gates que, fechados por padrão local/CI, desviam para fixture/no-op, raramente comunicado na UI (exceção: Executivos avisa "motor indisponível"). Processual, Playbooks, Agentes e Grupos Meta têm cadeia completa e passam nos 156 testes rodados. LegalArtifacts é raso: só texto versionado, sem DOCX/protocolo (Onda 6 não começou).
- **Plataforma** — ~30 capacidades mapeadas, maioria OPERACIONAL com cadeia completa e testada. Achados: 1 endpoint órfão (migração de tickets legados); 17 arquivos `* 2.py` duplicados; **crítico: em produção todo login Blackbox retorna 503 (MFA não validada)**; painel de incidentes da Onda 1 de segurança do Plano Mestre não localizado (AUSENTE).

---

## Tabela — COMERCIAL

| Capacidade | Estado | Evidência | Elo faltante |
|---|---|---|---|
| Funil comercial (Kanban) — mover card entre etapas | operacional | FunnelPage.tsx→klmApi.moveFunnel→PATCH /api/v1/klm/funnel/{matter_id}/stage (klm_operations.py:737-877); testes concorrência/idempotência em tests/api/test_klm_operations.py:2164-2330 | — |
| Leitura do funil com máscara de dados sensíveis (RBAC PII) | operacional | GET /api/v1/klm/funnel (klm_operations.py:704-712) aplica mask_klm_funnel se can_view_sensitive_lead_data=False; require_permission('kanban','view') | — |
| Criação de lead/cliente/matter a partir do funil | operacional | FunnelPage.tsx→klmApi.createClient/createMatter→POST /api/v1/clients e /api/v1/matters; wired em FunnelPage.tsx:461-476 | — |
| Orquestração de agentes pós-move de card | operacional | klm_operations.py:792-877 dispara KlmAgentService.execute por etapa, nunca reverte move em falha, audita funnel_move_automation_failed | — |
| Projeção econômica do caso (fee, % êxito, valor) | parcial | LeadPage.tsx→klmApi.updateFinancialProjection | PATCH /matters/{id}/financial-projection (klm_operations.py:715-734) exige só require_permission('kanban','edit') — mesma permissão genérica de mover card, sem permissão financeira dedicada |
| Lista de Leads (LeadPage) | operacional | LeadPage.tsx consome klmApi.funnel/threads/matter/messages/legalPricingPolicy; reaproveita endpoints já validados do funil | — |
| Conversas — inbox omnichannel unificado | operacional | ConversationsPage.tsx→klmApi.threads/filteredThreads/omnichannelContext→GET /api/v1/klm/omnichannel/threads (klm_operations.py:1343-1440); tests/services/test_omnichannel_service_window.py | — |
| Envio de mensagem humana (WhatsApp/Instagram/Messenger/Interna) | operacional | ConversationsPage.tsx:1872→klmApi.sendMessage→POST /threads/{id}/messages (klm_operations.py:1487); validação canal×direção em _authenticated_message_payload | — |
| Colaboração em conversa (participantes, menções, transferência) | operacional | ConversationCollaborationBar.tsx→klmApi.conversationParticipants/addConversationParticipant/transferOmnichannelThread; gated por assinatura | — |
| Métricas de resposta de conversa (SLA) | operacional | klmApi.conversationResponseMetrics→GET .../response-metrics (conversation_response_metrics.py:30-34) | — |
| Copiloto de IA na conversa | operacional | ConversationCopilotPanel.tsx→klmApi.omnichannelCopilot + createMatterTask; tipo KlmOmnichannelCopilot em api.ts:1771 | — |
| Fila de follow-up (criar/listar/atualizar) | parcial | GET/POST/PATCH /api/v1/klm/follow-ups (klm_operations.py:1654-1715); ConversationsPage.tsx:2067-2095 | Nenhuma task em celery_app.py referencia FollowUp — sem disparo automático quando vence, ao contrário de reuniões |
| Disponibilidade de equipe (staff availability) | operacional | ConversationsPage.tsx→klmApi.staffAvailabilityState/createStaffAvailabilityRequest/decideStaffAvailabilityRequest→staff_availability.py, gated por assinatura | — |
| Agenda interna — CRUD de reuniões | operacional | AgendaPage.tsx→klmApi.createMeeting/updateMeeting/deleteMeeting→/api/v1/meetings (meetings.py:31-92); tests/integration/test_meeting_concurrency_postgresql.py | — |
| Lembrete automático de reunião | operacional | Beat 'send-meeting-reminders-every-5-minutes'→notification_tasks.send_meeting_reminders (celery_app.py:166-169); MeetingService.list_due_reminders | — |
| Link de videoconferência automático em reunião | ausente | app/services/meeting_service.py:451-482 | meeting_service.py só valida meeting_url informado manualmente; nenhuma chamada a API de calendário/conferência para gerar o link |
| Link de agendamento público (booking link) — criar/revogar | operacional | AgendaSettingsPage/LeadPage/AgendaPage→BookingLinkDialog→klmApi.createBookingLink/revokeBookingLink→POST /api/v1/agenda/booking-links (agenda.py:101-166) | — |
| Página pública de agendamento (/agendar/:token) | operacional | App.tsx:272→KlmPublicBookingPage.tsx→klmApi.publicBookingPage/createPublicBooking→/api/v1/agenda/public/{token} (agenda.py:198-220) | — |
| Briefing de reunião e briefing diário (SDR/closer) | operacional | AgendaPage.tsx→klmApi.meetingBriefing/dailyBriefing→GET /api/v1/agenda/meetings/{id}/briefing e /daily-briefing (agenda.py:168-196) | — |
| Status de qualificação SDR e reprocessamento | **órfão** | grep em frontend/src por '/sdr/' e 'sdrQualification' = zero ocorrências fora do próprio endpoint backend | Backend completo (sdr.py:28-90) com RBAC e mask_sdr_status, mas frontend/api.ts não expõe wrapper para '/sdr/'; nenhuma página lê sdrQualification/sdrStep |
| Proposta comercial — geração e preview (/proposta/:matterId) | operacional | ConversationProposalPreparationSheet cria artifact kind='proposal' via createLegalArtifact→POST /legal-artifacts (klm_operations.py:2150); ProposalPreviewPage.tsx:109 | — |
| Contratos — tela de fechamento | parcial | App.tsx:324→KlmOperationalModulePage; OperationalModulePage.tsx:100,619,1030 | Rota 'contratos' não tem página dedicada, reaproveita OperationalModulePage genérica (mesma de 'peticoes'); assinatura via 'zapsign' sem indicar pré-requisito na UI |
| Assinatura eletrônica de contrato via ZapSign | parcial | app/services/zapsign_service.py:30-171, consumido só por integrations.py e webhooks.py | ZapSignService._is_configured() depende de token por workspace; sem token as chamadas falham e a UI não sinaliza esse pré-requisito antes de tentar enviar |
| Flows/automações vinculados a card do funil e conversa | operacional | FunnelCardWorkspaceSheet/ConversationsPage→klmApi.runFlow/pauseFlowRun/resumeFlowRun/cancelFlowRun→router flows.py | — |
| Trilha de auditoria KLM (eventos de funil/conversa) | operacional | GET /api/v1/klm/activity (klm_operations.py:2283) alimentado por record_audit em move_funnel_matter e falhas de automação | — |
| Dashboard/KPIs executivos comerciais | operacional | GET /api/v1/klm/dashboard e /api/v1/klm/kpis (klm_operations.py:632-662), read model executivo calculado no banco | — |
| Bootstrap local de mensagens simuladas | simulado | klm_operations.py:376-404, _authenticated_message_payload, gated por uses_local_infrastructure+is_local_only+KLM_LOCAL_BOOTSTRAP_ENABLED | metadata.localSimulation=True quando 3 flags de ambiente local coexistem; sem marcação visível na UI de conversas distinguindo essas mensagens de tráfego real |

## Tabela — FINANCEIRO

| Capacidade | Estado | Evidência | Elo faltante |
|---|---|---|---|
| Cobrança avulsa (charges) — criar/listar/cancelar/PIX | operacional | FinanceChargeDialog.tsx→klmApi→finance_charges.py (5 rotas, require_finance_access)→finance_charge_service.py→tabela finance_charges. Fail-closed HTTP 424 sem Asaas. Teste: test_finance_charges.py | — |
| Parcelamento de lançamento (installments) | operacional | FinanceChargeDialog.tsx:76 createFinanceInstallmentSeries→POST /finance/entries/{id}/installments (finance_recurrences.py:152-158). Teste: test_finance_charge_installments.py | — |
| Recorrência nativa — gestão direta (listar/pausar/retomar/encerrar) | **órfão** | grep createFinanceRecurrence/listFinanceRecurrences/pauseFinanceRecurrence no frontend não retorna nada | 6 de 8 rotas de finance_recurrences.py (linhas 29-150) sem função em klm/api.ts nem componente que chame |
| Recorrência via honorário retainer (criação indireta) | parcial | Criação completa (Honorários→FeeService→FinanceRecurrenceService→tabela) | FeeArrangementsPanel.tsx:217 só mostra toast; sem tela p/ ver/pausar/encerrar a recurrence criada por fee_service.py:265 |
| Régua de cobrança (dunning) automática D+1/D+7/D+15 | operacional | FinanceDunningCard.tsx→finance_dunning.py→worker dunning_tick agendado (crontab hour=8). Aprovação usa auth.user_id. Teste: test_finance_dunning_tick.py | — |
| Conciliação bancária — import OFX/CSV + confirmar/rejeitar linha | operacional | ReconciliationPanel.tsx→bank_reconciliation.py→reconciliation_service.py→bank_transactions/reconciliation_matches. Teste: test_reconciliation_service.py | — |
| Conciliação — extrato Asaas (Via B, pull automático) | **órfão** | grep 'import/asaas'/'pullAsaasStatement' no frontend não retorna nada | POST /finance/reconciliation/import/asaas (bank_reconciliation.py:74-90) sem chamada em klm/api.ts nem botão no painel |
| Conciliação — confirmar/rejeitar em lote | **órfão** | grep 'confirm-batch'/'reject-batch' no frontend não retorna nada | POST /finance/reconciliation/matches/confirm-batch e /reject-batch (linhas 138-199) sem equivalente em klm/api.ts; painel só confirma/rejeita individualmente |
| Harmonização de documentos financeiros | operacional | FinanceHarmonizationCard→finance_harmonization.py (7 rotas)→finance_harmonization_service.py→runs/items/quotas. Teste: test_finance_harmonization_service.py | — |
| Honorários F9 — arranjos fixo/retainer/timesheet/êxito | operacional | FeeArrangementsPanel.tsx→fee_arrangements.py (10 rotas)→fee_service.py→fee_arrangements/time_entries. Teste: test_fee_arrangements.py | — |
| Honorários F9 — aprovação de evento de êxito | parcial | fee_arrangement.py:86-87 (Optional[UUID]=None sem fallback a AuthContext) | approve_success_fee grava resolved_by do payload; frontend envia null (FeeArrangementsPanel.tsx:281) — autor da aprovação nunca é persistido |
| Contract billing F11 — extração de contrato → nós em rascunho | operacional | ContractBillingProposalsPanel.tsx→contract_billing.py:44-102→contract_billing_service.py→contract_billing_proposals. Teste: test_contract_billing_service.py | — |
| Contract billing F11 — aprovação (materializa cobranças) | parcial | contract_billing.py:52-57, schemas Optional[UUID]=None sem derivar de AuthContext | submit/approve_billing_proposal usam requested_by/resolved_by do payload; frontend envia null (linhas 120,134) — auditoria de aprovador NULL |
| Relatórios F10 — KPIs, DRE, fluxo de caixa projetado | operacional | FinanceOverviewPanel.tsx/FinanceAgingCard.tsx→finance_reports.py (3 rotas)→finance_reports_service.py. Teste: test_finance_reports_service.py | — |
| Relatórios F10 — export CSV/JSON e relatório Word timbrado | ausente | grep 'export'/'csv'/'docx'/'word' no endpoint e no service não retorna nada | Nenhuma rota de export em finance_reports.py; prometido no plano §F10 sem código em nenhuma camada |
| Stripe — checkout session + billing portal do workspace (F5) | **órfão** | grep 'checkoutSession'/'StripeCheckout' no frontend vazio; PlatformBillingPanel.tsx:154 diz 'Stripe liga depois pelo webhook' | stripe_billing.py:32-58 montado (router.py:232) mas sem chamada no frontend; Checkout.tsx é só cadastro, sem botão Stripe |
| Stripe — webhook de eventos de assinatura | **órfão** | stripe_webhook.py:1-16 sem trigger de sessão real | Processa eventos corretamente (HMAC, dedupe) mas sem checkout session criado pela UI o evento nunca dispara em uso real (consequência do item acima) |
| Asaas — gateway de pagamento (F1) + webhook de cobrança | operacional | asaas_webhook.py (POST /webhooks/asaas, público) dedupe de eventos; usado por finance_charges e local_billing. Teste: test_asaas_webhook.py | — |
| Local Billing (assinatura SaaS via Asaas — cobrança real) | operacional | Settings.tsx→SaasBillingPanel.tsx→local_billing.py (15 rotas)→local_billing_service.py. Testes: test_local_billing.py, test_local_billing_concurrency_postgresql.py | — |
| Benefícios/cupons — lado workspace (resgatar, indicação) | operacional | BenefitsPage.tsx→saasBenefitsApi.workspaceOverview/redeemCoupon/claimReferral→saas_benefits.py | — |
| Benefícios/cupons — lado plataforma (criar/editar cupom) | operacional | BlackboxApp.tsx:28-29,260-268→BlackboxCouponStudio.tsx→platform_router (saas_benefits.py). Teste: test_saas_benefits_access.py | — |
| Indicações com comissão (F13) — parceiros, aprovação | operacional | ReferralsPanel.tsx→referral_partners/matter_referrals/referral_commissions; comissão nasce como despesa pending_approval, conforme F13 | — |
| Despesas (F8) — avulsa/recorrente/reembolsável/aprovação | operacional | finance_expenses.py (5 rotas)→finance_expense_service.py, usado por conciliação e Indicações. Teste: test_finance_expenses.py | — |
| Pausa por upgrade/downgrade de plano (gate financeiro) | operacional | FinanceUpgradePauseNotice.tsx no topo da aba financeiro (OperationalModulePage.tsx:971). Teste: test_financial_upgrade_pause.py | — |
| Página pública de pagamento /pagar/{token} | ausente | Cliente recebe invoice_url hospedado pelo próprio Asaas (fake_adapter.py:116), nunca uma rota própria | Plano §F2 promete página própria molde /agendar/{token}; não existe rota '/pagar' em App.tsx |

## Tabela — JURÍDICO-AGENTES

| Capacidade | Estado | Evidência | Elo faltante |
|---|---|---|---|
| Processual — cadastro/listagem de processos monitorados (CNJ) | operacional | process_monitoring.py + process_monitoring_service.py + models/process_monitoring.py + ProcessosPage.tsx; RBAC processos.view/manage; 33 testes passando | — |
| Processual — ingestão diária DJEN/DataJud | simulado | djen_datajud_clients.py + process_monitoring_tasks.py | Gate fechado por padrão faz worker ler tests/fixtures/djen/*.json. ProcessosPage.tsx:86 descreve como leitura real, sem indicar modo simulado |
| Processual — cálculo de prazo com memória auditável | operacional | prazo_calculator.py + prazo_texto_heuristica.py; endpoints confirm/dismiss deadline; DeadlineMemoryDialog no frontend exibe a memória | — |
| LegalArtifacts — versionar minuta (draft/criar/listar) | operacional | klm_operations.py:2149 create_legal_artifact + klm_operations_service.py:5473; tabela LegalArtifactVersion; LegalArtifactsPanel.tsx em /app/peticoes e /app/contratos | — |
| LegalArtifacts — envio para aprovação humana | operacional | klm_operations.py:2172 submit_legal_artifact → service:5552 cria ApprovalQueueItem; 1 teste em test_klm_operations.py | — |
| Petições — geração/exportação de documento (DOCX/protocolo) | ausente | Nenhuma referência a DOCX/Word/protocolo em app/services ligada a LegalArtifact; PLANO_MESTRE_2026-07-24.md lista a onda como pendente | content é Dict livre (schemas:1083); painel só grava {body, editor:'klm_native'} texto simples. Sem endpoint/serviço de export Word/PDF ou protocolo (Onda 6 pendente) |
| Agentes — registry (pausar/retomar, tools por agente) | operacional | klm_operations.py:950-1025 → KlmAgentService; AgentsPage.tsx; test_klm_agents.py passando | — |
| Agentes — autonomia (human-in-the-loop) | operacional | klm_operations.py:1027-1082, require_workspace_admin + ETag; AgentAutonomyCard.tsx em AgentsPage.tsx:519; teste dedicado passando | — |
| Agentes — runs auditados + retry/override humano | operacional | agent_runtime.py montado em router.py:165; services/matters.ts:352,362; testes de api e service passando | — |
| Agentes — governança hierárquica entre agentes | operacional | agent_governance_service.py:200+ consumido por klm_agent_service.py e agent_context_service.py; motor server-side sem tela dedicada (correto); teste dedicado passando | — |
| Agentes — playbooks (draft→submit→approve→publish→rollback→simulate) | operacional | playbooks.py (12 rotas) → playbook_service.py; PlaybookEditorPage.tsx usa todas as ações; 3 arquivos de teste passando | — |
| Agentes — knowledge base (playbooks de área no contexto) | operacional | app/agents/knowledge/*.py importados por agent_context_service.py; sem tela própria (é insumo interno do runtime, coerente); tests/agents/* passando | — |
| Página Conhecimento (/app/conhecimento) — artigos de ajuda | parcial | Nenhum apiFetch/klmApi no arquivo; único I/O é window.localStorage | Conteúdo estático, sem chamada de API; feedback (thumbs up/down) só em localStorage (KnowledgePage.tsx:35,52), nunca enviado ao backend. Sistema distinto do agents/knowledge usado pelos agentes |
| Executivos — threads/mensagens/compartilhamento/fontes/relatórios | operacional | executives.py (20+ rotas) → executive_service.py; ExecutivesPage.tsx; testes api+service+permissions passando (require_workspace_admin) | — |
| Executivos — motor de IA (resposta do conselho C-level) | ausente | executives.py:610-660; ExecutivesPage.tsx exibe 'indisponível até homologação do motor e RAG' | executives.py:580-663 create_message sempre grava ExecutiveRound status=failed com aviso fixo. Nenhuma chamada a LLM/agent runtime. Único módulo do escopo que avisa a lacuna na UI |
| Grupos Meta — criação/gestão local (registro interno) | operacional | meta_groups.py create_local_meta_group + meta_groups_service.py:520; GroupsPage.tsx; testes api+service passando | — |
| Grupos Meta — criação/ativação oficial via Graph API | parcial | meta_groups_service.py:153-237; GroupsPage.tsx:261,290,304,338 mostram blockedReasons visíveis | evaluate_capability (l.153) combina developer_app_ready+oba_gate_open+external_delivery_allowed; fechado, só persiste intenção local. UI expõe gate/blockedReasons: declarado, não oculto |
| Grupos Meta — enviar msg, rotacionar convite, fixar/remover participante | parcial | GroupsPage.tsx:466-497,530-571 mostra estados 'aguardando capability' e 'preparada localmente' | Dependem do gate de capability oficial; local ficam 'preparado localmente'/'aguardando capability', visível na UI, sem efeito real sem credenciais Meta completas |
| Agentes — TiAgentPage (/app/ti) | parcial | App.tsx:332 <Route path="ti" ... KlmTiAgentPage />; fora do foco principal desta onda | Rota registrada (module=admin) mas não auditada em profundidade nesta onda; recomenda checagem dedicada para confirmar se aponta a agente real ou é vitrine |

## Tabela — PLATAFORMA

| Capacidade | Estado | Evidência | Elo faltante |
|---|---|---|---|
| Conta 360 (WorkspaceAccount360) | operacional | WorkspaceAccount360.tsx -> fetchWorkspaceOverview -> GET /platform/workspaces/{id}/overview (platform_admin.py:1072) -> WorkspaceOverviewService; gate require_platform_root; testado | — |
| Overrides de entitlement | operacional | putWorkspaceEntitlementOverrides -> PUT /platform/workspaces/{id}/entitlement-overrides (platform_admin.py:1117) -> tabela workspace_entitlement_overrides; auditado; testes cobrem upsert/delete/leak | — |
| SAC / tickets de suporte | operacional | /blackbox/support-tickets -> PlatformSupportTicketsPanel.tsx -> platform_support.py (15 rotas) -> SupportTicketService | — |
| Escalonamento de SAC (fora horário) | operacional | SupportEscalationSettingsSection -> GET/PUT /platform/support/escalation-settings (platform_support.py:125-165) | — |
| Continuidade/substituição de equipe | operacional | /blackbox/team/continuity -> StaffContinuityPanel -> staff_reassignment.py -> worker staff_reassignment_tasks.py com beat 5min; testado em tests/staff_reassignment/ | — |
| Migração de tickets técnicos legados | **órfão** | POST /platform/support/migrate-technical-requests (platform_support.py:440) testado; grep no frontend por essa rota não retornou nada | Endpoint testado mas sem chamada no frontend |
| Billing SaaS - Stripe | operacional | stripe_billing.py (checkout-session/portal-session) + stripe_webhook.py -> stripe_billing_service, fail-closed via external_delivery_allowed(); testes test_stripe_billing.py, test_stripe_webhook_security.py | — |
| Gestão de assinaturas no Blackbox | operacional | Aba Financeiro -> PlatformBillingPanel -> changeSubscriptionPlan/applySubscriptionDiscount/extendSubscriptionTrial/suspend/reactivate (platform.ts) -> platform_admin.py /subscriptions* | — |
| Billing local (self-hosted) | operacional | local_billing.py (catalog/quote/portal/intents) -> local_billing_service; testes API+service+concorrência PostgreSQL | — |
| Site público - CMS | operacional | PlatformSitePanel -> platform_site.py (11 rotas) -> publicado e consumido por Landing.tsx via fetchPublicHome -> GET /public/home (public_site.py:19) | — |
| Blog - CMS + público | operacional | PlatformBlogPanel -> platform_blog.py (25 rotas) -> consumido por Blog.tsx/BlogPost.tsx/BlogCategory.tsx via public_blog.py | — |
| Biblioteca de mídia da plataforma | operacional | PlatformMediaLibraryPanel -> fetchPlatformBlogMediaAssets/create/confirm (platform.ts:670-685) -> platform_blog.py | — |
| SEO e indexação (blog) | operacional | PlatformSeoIndexingPanel -> fetchPlatformBlogIndexing/save (platform.ts:649-661) -> platform_blog.py | — |
| Google Business Profile | operacional | PlatformGoogleBusinessPanel -> connection/jobs/queue job (platform.ts:687-712) | — |
| WhatsApp oficiais do SaaS | operacional | PlatformOfficialWhatsAppPanel -> connect/disconnect/setActive/test (platform.ts:713-830) -> platform_official_whatsapp.py; testado | — |
| Configuration Studio (agentes/fluxos por conta) | operacional | /blackbox/configuration-studio -> platform-configuration-studio.ts -> platform_configuration_studio.py (6 rotas); testado | — |
| Planos e políticas comerciais | operacional | /blackbox/plans -> PlatformPlanPoliciesPanel -> platform_plan_policies.py + saas_benefits platform_router | — |
| Cupons (Studio de cupons) | operacional | /blackbox/coupons -> services/saas-benefits.ts -> saas_benefits.py platform_router (22 rotas); testado test_saas_benefits_access.py | — |
| Indicações e benefícios (Partner) | operacional | /blackbox/benefits -> BlackboxBenefitsStudio.tsx -> saas-benefits.ts -> saas_benefits.py | — |
| Desempenho/gamificação de equipes | operacional | /blackbox/performance -> blackbox-studios.ts -> GET /platform/performance/{workspace-team,saas-team,benchmark} (platform_blackbox_studios.py:53-88); testado | — |
| Biblioteca de campanhas (root only) | operacional | /blackbox/creative-library -> GET/PUT /platform/creative-library[/items] (platform_blackbox_studios.py:88-112) | — |
| Relatórios avançados | operacional | /blackbox/advanced-reports -> fetchAdvancedReport -> GET /platform/reports (platform_blackbox_studios.py:133) | — |
| Catálogo de integrações | operacional | GET /integrations/catalog (integrations.py:86) usado por Settings.tsx/Admin.tsx; não tem tela própria em /blackbox | — |
| Webhook de leads de sites (F12) | operacional | POST /webhooks/leads/{token} (lead_webhooks.py:34) fail-closed; config_router site-leads consumido em ConnectionsHubPage.tsx | — |
| Webhook de leads do Google | operacional | google_lead_webhooks.py incluído no router; worker lead_hydration_tasks.py com beat 1min; testado tests/workers/test_lead_hydration_tasks.py | — |
| Tracking conversão Google/Meta Ads (CAPI) | operacional | PaidAcquisitionPage.tsx -> paid_acquisition.py (5 rotas) -> ads_feedback_tasks.py (beat 1min), fail-closed com recibo simulado rotulado; UI expõe estado do gate | — |
| Workers Celery + beat schedule | operacional | celery_app.py define 20+ beat_schedule; docker-compose(.prod).yml tem serviços worker/beat com healthcheck | — |
| Provisionamento local | operacional | local_provisioning.py (plan/status/apply/reconcile), gateado por assinatura no router.py | — |
| Isolamento de superfície Blackbox | operacional | BlackboxApp.tsx (BlackboxBoundary/PlatformRoute) + platform_access.py deriva superfície do banco, não da rota | — |
| MFA obrigatório p/ login interno em produção | parcial | auth.py:329-338: if platform_identity and settings.is_production: raise 503 AUTH-BLACKBOX-MFA-NOT-READY | Bloqueio existe e funciona, mas impede acesso a TUDO acima em produção (fail-closed correto, mas paralisante) |
| Painel de incidentes/abuso (Onda 1 seg. Plano Mestre) | ausente | Busca por rate-limit/shadow-mode/incident/abuse não retornou router ou serviço correspondente no escopo investigado | Rate-limit por rota + shadow mode + painel de incidentes descritos no Plano Mestre não encontrados no código |

---

## DORMENTES E ÓRFÃOS (lista de caça para a auditoria final de UX)

Backend com código real, testado, mas com elo faltante para o usuário final — priorizar na varredura de UX/QA. Ordenado por área.

### Comercial
1. **Status de qualificação SDR e reprocessamento** — órfão total: backend (sdr.py) com RBAC/mask/testes, zero consumo no frontend.
2. **Fila de follow-up** — parcial: existe CRUD, mas sem worker que dispare quando vence (diferente de reuniões, que têm beat de lembrete).
3. **Projeção econômica do caso** — parcial: RBAC genérico de kanban em vez de permissão financeira dedicada.
4. **Contratos — tela de fechamento** — parcial: reaproveita componente genérico de Petições, sem página própria.
5. **Assinatura eletrônica via ZapSign** — parcial: depende de token por workspace sem checagem/aviso na UI antes do envio.

### Financeiro
6. **Recorrência nativa — gestão direta** — órfão: 6 de 8 rotas de finance_recurrences.py sem qualquer chamada no frontend.
7. **Recorrência via honorário retainer** — parcial: cria a recorrência mas não há tela para ver/pausar/encerrar.
8. **Conciliação — extrato Asaas (Via B, pull automático)** — órfão: rota pronta, sem botão no painel.
9. **Conciliação — confirmar/rejeitar em lote** — órfão: rotas de batch prontas, painel só opera individualmente.
10. **Honorários F9 — aprovação de evento de êxito** — parcial: frontend envia `resolved_by=null`, aprovador nunca é persistido.
11. **Contract billing F11 — aprovação** — parcial: mesmo problema, `requested_by`/`resolved_by` chegam null.
12. **Stripe — checkout session + billing portal do workspace (F5)** — órfão: endpoints prontos, nenhuma tela chama; caminho real de cobrança é Local Billing/Asaas.
13. **Stripe — webhook de eventos de assinatura** — órfão (consequência do item 12): processa corretamente, mas nunca é acionado em uso real.

### Jurídico-Agentes
14. **Página Conhecimento (/app/conhecimento)** — parcial: conteúdo estático, feedback do usuário só em localStorage, nunca chega ao backend.
15. **Grupos Meta — criação/ativação oficial via Graph API** — parcial: gate fechado por padrão, só persiste intenção local (UI já avisa).
16. **Grupos Meta — enviar msg/rotacionar convite/fixar participante** — parcial: mesma dependência do gate oficial Meta.
17. **Agentes — TiAgentPage (/app/ti)** — parcial: rota registrada, não auditada em profundidade; checar se aponta a agente real.

### Plataforma
18. **Migração de tickets técnicos legados** — órfão: endpoint testado, sem botão/tela no Blackbox.
19. **MFA obrigatório p/ login interno em produção** — parcial: bloqueio correto (fail-closed), mas paralisa acesso a todo o Blackbox em produção até homologação de MFA.

---

## RISCOS CRÍTICOS (agregados e deduplicados)

1. **[CRÍTICO — Plataforma] Login Blackbox bloqueado em produção**: `auth.py:329-338` retorna 503 quando `platform_identity` + `settings.is_production=True`. Toda a Central Blackbox (SAC, financeiro, CMS, cupons etc.) fica inacessível em prod até a MFA ser validada — o inventário de plataforma reflete apenas dev/local.
2. **[CRÍTICO — Financeiro] Aprovações que geram dinheiro sem autor registrado**: `approve_success_fee` (F9) e `approve_billing_proposal` (F11) gravam `resolved_by`/`requested_by = NULL` porque o frontend envia `null` no payload em vez de derivar do `AuthContext` — ao contrário de `finance_dunning.py` (linhas 96-102), que usa `auth.user_id` corretamente. Quebra de trilha de auditoria em decisões financeiras.
3. **[ALTO — Comercial] RBAC insuficiente na projeção financeira do caso**: `PATCH financial-projection` (klm_operations.py:715-734) altera fee/%/valor exigindo só a permissão genérica `kanban:edit`, sem permissão financeira dedicada.
4. **[ALTO — Comercial] Follow-ups sem disparo proativo**: nenhuma task no Celery Beat varre follow-ups vencidos, ao contrário de reuniões (`send-meeting-reminders-every-5-minutes`).
5. **[ALTO — Comercial] SDR 100% órfão no frontend**: nenhuma tela consome status/qualificação, apesar de RBAC e testes existirem no backend — recurso morto do ponto de vista do usuário.
6. **[MÉDIO — Comercial] `localSimulation=True` sem indicação na UI**: ativa quando 3 flags de ambiente local coexistem (`klm_operations.py:376-404`); confirmar que nunca coexistem em produção, pois mascarariam dados simulados como reais na tela de conversas.
7. **[MÉDIO — Comercial] ZapSign sem checagem proativa de token**: risco de falha silenciosa no fechamento de contrato se o workspace não tiver token configurado, sem aviso prévio na tela.
8. **[MÉDIO — Comercial] Reunião sem link de vídeo automático**: sem integração Meet/Zoom; depende de URL colada manualmente, validada só por regex/allowlist.
9. **[BAIXO — Comercial] 'Contratos' reaproveita componente genérico de 'Petições'**: sem página dedicada; mudanças futuras em Petições podem quebrar Contratos sem intenção.
10. **[MÉDIO — Financeiro] Bloqueio fail-closed sem aviso proativo**: sem credencial Asaas, cobrança/recorrência/conciliação Via B ficam bloqueadas com HTTP 424 (comportamento correto) — confirmar que o onboarding avisa isso antes do usuário tentar usar.
11. **[BAIXO — Financeiro] Checkout Stripe do workspace é código morto em produção**: endpoints prontos sem tela chamando; caminho real de cobrança é Local Billing/Asaas.
12. **[BAIXO — Financeiro/Plataforma] Arquivos duplicados " 2.py"**: 16 arquivos idênticos em benefícios/cupons (models, schemas, endpoint, service, db, testes espelhados) + 17 arquivos "* 2.py" adicionais no escopo de plataforma (ex.: `saas_benefits 2.py`) — mesma classe de lixo já identificada na Onda 0.2 do Plano Mestre; não quebra cadeias, mas é dívida de higiene a limpar.
13. **[MÉDIO — Financeiro] Gestão de recorrência e conciliação via Asaas/lote sem UI**: correção hoje exige acesso direto a banco/API, sem trilha de auditoria de UI.
14. **[MÉDIO — Jurídico] Ingestão DJEN/DataJud pode estar simulada sem aviso**: gate fechado por padrão faz o worker ler fixtures; a UI (ProcessosPage.tsx:86) descreve como leitura real, sem indicar modo simulado — usuário pode achar que prazos vêm de fonte real.
15. **[MÉDIO — Jurídico] RBAC/UX descasado em LegalArtifacts**: `LegalArtifactsPanel.tsx` não checa permissão local; botões aparecem para qualquer usuário com `view`, mas o backend exige `kanban.edit`/`aprovacoes.approve` — gera 403 sem aviso prévio.
16. **[MÉDIO — Jurídico] Entitlement de 'Processos' sem checagem de quota visível**: é add-on pago, mas não há controle de cobrança/quota visível além do escopo por `workspace_id` — confirmar controle real vs. só texto de UI.
17. **[MÉDIO — Jurídico] Executivos: RBAC plano demais**: toda a superfície exige só `require_workspace_admin` — uma role customizada que ganhe `workspace_admin` por engano herda acesso total a threads de outros admins.
18. **[BAIXO — Jurídico] `expected_version` opcional em LegalArtifact**: checar corrida entre edições concorrentes do mesmo `artifact_key` quando o frontend não envia `expected_version`.
19. **[BAIXO — Jurídico] Feedback da página Conhecimento só em localStorage**: perdido ao trocar de dispositivo; produto sem visibilidade agregada de quais artigos funcionam.
20. **[BAIXO — Jurídico] Gate de Grupos Meta oficial concentrado em 1 booleano**: `external_delivery_allowed` combinado com 2 outras condições — validar que nunca fica `true` fora de produção aprovada.
21. **[ALTO — Plataforma] Painel de incidentes/abuso da Onda 1 de segurança não localizado**: rate-limit por rota + shadow mode + painel de incidentes descritos no Plano Mestre não têm router/service correspondente no código.
22. **[BAIXO — Plataforma] Endpoint órfão de migração de tickets legados**: só acionável via HTTP direto por engenharia, sem tela no Blackbox.

---

## Contagem final por estado

| Estado | Comercial | Financeiro | Jurídico-Agentes | Plataforma | **Total** |
|---|---|---|---|---|---|
| operacional | 20 | 15 | 12 | 28 | **75** |
| parcial | 4 | 3 | 4 | 1 | **12** |
| órfão | 1 | 5 | 0 | 1 | **7** |
| ausente | 1 | 2 | 2 | 1 | **6** |
| simulado | 1 | 0 | 1 | 0 | **2** |
| **Total de capacidades mapeadas** | 27 | 25 | 19 | 31 | **102** |

**Leitura geral**: 75/102 (≈74%) das capacidades mapeadas na Onda 1 estão operacionais de ponta a ponta, com RBAC e teste real. 19 capacidades (12 parciais + 7 órfãs) são "dormentes" — código existe mas o usuário final não consegue chegar até ele ou o elo tem uma falha específica. 6 capacidades estão prometidas no plano mas ausentes de código. 2 rodam em modo simulado sem aviso claro na UI. O achado transversal mais grave não é ausência de código — é RBAC genérico onde deveria haver permissão dedicada (projeção financeira, LegalArtifacts) e auditoria quebrada em decisões que geram dinheiro (approver=NULL em F9/F11).
