# ONDA 1 — BACKLOG DE DORMENTES E ÓRFÃOS (2026-07)

> Recorte só de capacidades **parcial** ou **órfão** identificadas na Matriz da Verdade da Onda 1 (`ONDA1_MATRIZ_DA_VERDADE_2026-07.md`). Cada item tem a ação recomendada: **criar superfície** (falta tela/botão no frontend para código de backend já pronto), **remover** (código morto, sem uso previsto), ou **ligar elo** (falha pontual de integração — RBAC, autoria, worker de disparo etc.). Nenhum código foi alterado nesta auditoria.

## Comercial

| # | Capacidade | Estado | Ação recomendada | Detalhe |
|---|---|---|---|---|
| 1 | Status de qualificação SDR e reprocessamento | órfão | **Criar superfície** | Backend completo (sdr.py:28-90) com RBAC e mask_sdr_status. Adicionar wrapper em klm/api.ts + tela/seção (LeadPage ou painel dedicado) consumindo `sdrQualification`/`sdrStep`. Se a decisão for descontinuar SDR como conceito de produto, então **remover** o backend em vez de deixá-lo órfão. |
| 2 | Fila de follow-up (disparo automático) | ~~parcial~~ **resolvido (2026-07-26)** | ~~Ligar elo~~ | Nova task `follow_up_tasks.process_due` (2 camadas: descoberta + fan-out por workspace, mesmo padrão de `send-meeting-reminders-every-5-minutes`), agendada em `celery_app.py` (`process-due-follow-ups-every-5-minutes`, a cada 5 min). `FollowUpTask.last_notified_at` (migração `45913a19da62`) garante idempotência: um follow-up só é notificado 1x por ciclo (`KlmOperationsService.list_due_follow_ups_for_notification`/`mark_follow_up_notified`). Envio real via `OperationalAlertService.send_due_follow_up_reminders` — fail-closed por `external_delivery_allowed()` nas 3 camadas (dispatch, processamento por workspace, `_dispatch_event`); gate fechado não marca `last_notified_at`, então o follow-up permanece pendente para o próximo tick. Nova regra `followUpDue` em `DEFAULT_NOTIFICATION_SETTINGS`/`NOTIFICATION_RULE_CATALOG`. Testes: `test_send_due_follow_up_reminders_dispatches_once_and_second_tick_is_noop`, `test_send_due_follow_up_reminders_gate_closed_leaves_follow_up_pending` (`tests/services/test_operational_alert_service.py`); `test_follow_up_process_due_sends_once_and_second_run_is_noop`, `test_follow_up_dispatcher_enqueues_only_active_workspaces_with_due_items`, gate/validação de workspace (`tests/workers/test_follow_up_tasks.py`). |
| 3 | Projeção econômica do caso (fee/%/valor) | ~~parcial~~ **resolvido (2026-07-26)** | ~~Ligar elo~~ | `PATCH /matters/{id}/financial-projection` (klm_operations.py) agora exige `kanban:edit` **e** `finance:manage` (RBAC dedicado, ver `app/core/rbac.py`). Testes: `test_financial_projection_requires_finance_permission_even_with_kanban_edit` e `test_financial_projection_allows_role_with_kanban_and_finance_permission` em `tests/api/test_klm_operations.py`. |
| 4 | Contratos — tela de fechamento | parcial | **Criar superfície** | Extrair de OperationalModulePage.tsx uma página própria para 'contratos', desacoplada de 'peticoes', e sinalizar na UI o pré-requisito de token ZapSign configurado. |
| 5 | Assinatura eletrônica via ZapSign | parcial | **Ligar elo** | Adicionar checagem de `ZapSignService._is_configured()` (ou equivalente) exposta à UI antes de habilitar o botão de envio, com aviso explícito se o workspace não tiver token. |

## Financeiro

| # | Capacidade | Estado | Ação recomendada | Detalhe |
|---|---|---|---|---|
| 6 | Recorrência nativa — gestão direta (listar/pausar/retomar/encerrar) | órfão | **Criar superfície** | 6 de 8 rotas de finance_recurrences.py (linhas 29-150) sem função em klm/api.ts. Criar painel de gestão de recorrências (pode reaproveitar o card de honorários retainer, ver item 7). |
| 7 | Recorrência via honorário retainer (pós-criação) | parcial | **Criar superfície** | FeeArrangementsPanel.tsx:217 só mostra toast na criação. Adicionar tela/seção para ver, pausar e encerrar a recorrência criada por fee_service.py:265 — pode reusar a superfície do item 6. |
| 8 | Conciliação — extrato Asaas (Via B, pull automático) | órfão | **Criar superfície** | POST /finance/reconciliation/import/asaas (bank_reconciliation.py:74-90) pronto; adicionar botão "importar extrato Asaas" no ReconciliationPanel. |
| 9 | Conciliação — confirmar/rejeitar em lote | órfão | **Criar superfície** | Rotas confirm-batch/reject-batch (bank_reconciliation.py:138-199) prontas; adicionar seleção múltipla + ação em lote no ReconciliationPanel. |
| 10 | Honorários F9 — aprovação de evento de êxito (autoria) | ~~parcial~~ **resolvido (2026-07-26)** | ~~Ligar elo~~ | `approve_success_fee` (fee_arrangements.py) não aceita mais `resolvedBy` no payload — deriva sempre de `auth.user_id`, mesmo padrão de finance_dunning.py:96-102. `ApproveSuccessFeeRequest` removido do schema. Frontend (FeeArrangementsPanel.tsx:281, klm/api.ts) parou de enviar o campo. Testes: `test_success_event_requires_approval_before_billing` (agora com asserção de autoria) e `test_approve_success_fee_ignores_forged_resolved_by_in_payload` em `tests/api/test_fee_arrangements.py`. |
| 11 | Contract billing F11 — aprovação (autoria) | ~~parcial~~ **resolvido (2026-07-26)** | ~~Ligar elo~~ | Mesma correção do item 10 em `approve_billing_proposal` (contract_billing.py) — `ApproveProposalRequest` removido do schema, autor sempre `auth.user_id`. Frontend (ContractBillingProposalsPanel.tsx:134, klm/api.ts) parou de enviar `resolvedBy`. Testes: `test_extract_submit_approve_flow` (com asserção de autoria) e `test_approve_billing_proposal_ignores_forged_resolved_by_in_payload` em `tests/api/test_contract_billing.py`. Nota: `submitContractBillingProposal` (requestedBy) e os demais fluxos de `requested_by` (extract, register, renegotiate) ficaram fora deste fix — não são o risco CRÍTICO de autoria de aprovação financeira; permanecem como item futuro se o produto quiser rastrear "quem solicitou" com o mesmo rigor. |
| 12 | Stripe — checkout session + billing portal do workspace (F5) | órfão | **Remover (ou decidir e criar superfície)** | Endpoints prontos (stripe_billing.py:32-58) sem qualquer chamada no frontend; caminho real de cobrança é Local Billing/Asaas. Decisão de produto: se Stripe não é mais o caminho de billing do workspace, remover o router; se ainda é plano futuro, documentar como backlog explícito em vez de código morto silencioso. |
| 13 | Stripe — webhook de eventos de assinatura | órfão | **Remover (dependente do item 12)** | Consequência direta do item 12 — sem checkout real, o webhook nunca dispara em uso real. Mesma decisão do item 12 se aplica aqui. |
| 14 | Relatórios F10 — export CSV/JSON e relatório Word timbrado | ausente | *(fora do escopo deste backlog — é AUSENTE, não parcial/órfão; ver Matriz completa)* | — |
| 15 | Página pública de pagamento /pagar/{token} | ausente | *(fora do escopo deste backlog — é AUSENTE, não parcial/órfão; ver Matriz completa)* | — |

## Jurídico-Agentes

| # | Capacidade | Estado | Ação recomendada | Detalhe |
|---|---|---|---|---|
| 16 | Página Conhecimento (/app/conhecimento) — feedback de artigos | ~~parcial~~ **resolvido (2026-07-26)** | ~~Ligar elo~~ | Novo `POST /api/v1/knowledge/articles/{article_id}/feedback` (tabela `klm_knowledge_article_feedback`, migração `58f5e2e75303`, workspace_id+user_id+RLS, UNIQUE por workspace/usuário/artigo — upsert idempotente, não duplica voto). `KnowledgePage.tsx` (`handleFeedback`) trocou a escrita em `window.localStorage` por `klmApi.submitKnowledgeArticleFeedback`, com update otimista e reconciliação pela resposta do backend. Testes: `test_submit_feedback_persists_and_returns_aggregate_counts`, `test_submit_feedback_twice_by_same_user_updates_instead_of_duplicating`, `test_feedback_summary_is_isolated_per_workspace` (`tests/api/test_knowledge.py`); `sends thumbs up/down feedback to the backend instead of localStorage`, `keeps the optimistic vote when the feedback API call fails` (`frontend/src/test/knowledge-page.test.tsx`). |
| 17 | Grupos Meta — criação/ativação oficial via Graph API | parcial | **Ligar elo (dependência externa)** | Gate `evaluate_capability` (meta_groups_service.py:153-237) fechado por padrão até credenciais Meta completas. Não é bug de código — é dependência de homologação externa. Ação: acompanhar processo de aprovação Meta; UI já expõe `blockedReasons` corretamente, manter assim. |
| 18 | Grupos Meta — enviar msg/rotacionar convite/fixar participante | parcial | **Ligar elo (dependência externa)** | Mesma dependência do item 17 — sem ação de código até a capability oficial abrir. |
| 19 | Agentes — TiAgentPage (/app/ti) | parcial | **Investigar antes de decidir** | Rota registrada (App.tsx:332, module=admin) mas não auditada em profundidade nesta onda. Ação: sessão de auditoria dedicada para confirmar se aponta a um agente real (então documentar) ou é vitrine (então remover ou completar). |

## Plataforma

| # | Capacidade | Estado | Ação recomendada | Detalhe |
|---|---|---|---|---|
| 20 | Migração de tickets técnicos legados | órfão | **Criar superfície (ou remover se migração já concluída)** | POST /platform/support/migrate-technical-requests (platform_support.py:440) testado, sem chamada no frontend. Se a migração de tickets legados já foi executada e é uma operação pontual encerrada, remover/arquivar o endpoint; se ainda é necessária, adicionar botão de execução no painel de SAC do Blackbox restrito a root. |
| 21 | MFA obrigatório p/ login interno em produção | parcial | **Ligar elo** | auth.py:329-338 bloqueia (503) corretamente por fail-closed, mas paralisa acesso a toda a Central Blackbox em produção. Ação: priorizar a homologação do fluxo de MFA para desbloquear o acesso operacional real, mantendo o fail-closed como está (correto do ponto de vista de segurança). |

---

## Nota sobre higiene (fora do escopo parcial/órfão, mas achado recorrente nos 4 inventários)

Arquivos duplicados `* 2.py`/`* 2.md` byte-idênticos (16 em benefícios/cupons + 17 em plataforma, ex.: `saas_benefits 2.py`) não quebram nenhuma cadeia funcional, mas são dívida de limpeza recorrente (mesma classe de achado da Onda 0.2 do Plano Mestre). Ação recomendada: **remover** em uma varredura de higiene à parte, fora do escopo desta Onda 1.

## Resumo de contagem

- Total de itens dormentes/órfãos: **19** (excluindo os 2 itens "ausente" listados por completude nas linhas 14-15, que pertencem à categoria AUSENTE, não a este backlog).
- Ação **criar superfície**: 7 (itens 1, 4, 6, 7, 8, 9, 20)
- Ação **ligar elo**: 8 (itens 2, 3, 5, 10, 11, 16, 17/18, 21)
- Ação **remover / decidir**: 3 (itens 12, 13, 20-alternativa)
- Ação **investigar antes de decidir**: 1 (item 19)
