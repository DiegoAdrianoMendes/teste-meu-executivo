# Auditoria Final de UX — Capacidades Dormentes (Backend sem Tela)

**Data:** 2026-07-27
**Escopo:** consolidação de 3 levantamentos independentes cobrindo (A) Jurídico Ondas 2-8 + Migração, (B) SDR + Financeiro + Stripe, (C) Blackbox/Plataforma + Integrações de Ads + Workers.
**Método:** grep exaustivo no frontend (`klm/api.ts` e chamadas diretas) contra as rotas expostas no backend. Nenhum código foi alterado — auditoria de leitura apenas.

> **Distinção central desta auditoria:** nem toda rota sem tela é um bug. Uma parte grande do backend jurídico (Ondas 2-8) foi construída **antes** da onda de UI planejada para ele — isso é sequenciamento de roadmap, não dormência acidental. Os itens tratados aqui como **dormentes reais** são os que não têm (e não têm previsão anunciada de) UI, muitas vezes em áreas com risco financeiro ou de tracking de receita.

---

## 1. Tabela única — todos os dormentes, ordenada por prioridade de valor

A tabela agrupa primeiro os **dormentes reais** (ação recomendada: fechar em próxima sprint de UI), depois o bloco **backend-only por design** (Ondas 2-8 jurídicas, já mapeado para uma onda de UI futura). Dentro de cada bloco, a ordem segue impacto financeiro/risco/valor entregue, do maior para o menor.

| # | Categoria | Capacidade | Endpoint | Tem UI | Ação recomendada |
|---|---|---|---|---|---|
| 1 | 🔴 Dormente real | Meta Conversions API (CAPI) — despacho real de conversão paga | `ads_feedback_tasks.py` `load_meta_capi_config` (label `meta_capi`) | não | Criar endpoint `/connect/meta-capi` + card de status. Hoje só é ativável inserindo linha direto no banco (como os testes fazem) — tracking de conversão paga no Meta não funciona de verdade em produção sem isso. |
| 2 | 🔴 Dormente real | Google Ads Data Manager — upload de conversão offline | `google_conversions_service.py` (label `google_data_manager`, único caminho pós-15/06/2026) | não | Mesma lacuna do Meta CAPI: zero endpoint de criação da connection em toda a API. Sem tela, conversão paga para Google Ads não funciona em produção. Criar connect + card. |
| 3 | 🔴 Dormente real | Recorrência nativa — gestão direta (list/get/update/pause/resume/end) | `finance_recurrences.py:47-150` (6 das 8 rotas) | não | Criar painel de gestão de recorrências; reaproveitar como superfície única com o item #4. |
| 4 | 🔴 Dormente real | Recorrência via honorário retainer (pós-criação) | `fee_service.py:265` + `FeeArrangementsPanel.tsx:217` | parcial | Confirmado parcial: só toast na criação, sem tela para ver/pausar/encerrar. Virar aba na mesma superfície do item #3. |
| 5 | 🔴 Dormente real | Conciliação — extrato Asaas (pull automático) | `bank_reconciliation.py:74` (`POST /finance/reconciliation/import/asaas`) | não | `ReconciliationPanel.tsx` só usa import OFX/CSV manual. Adicionar botão "importar extrato Asaas". |
| 6 | 🔴 Dormente real | Conciliação — confirmar/rejeitar em lote | `bank_reconciliation.py:143,190` | não | Sem wrapper batch nem seleção múltipla no `ReconciliationPanel.tsx`. Criar seleção múltipla + ação em lote. |
| 7 | 🔴 Dormente real | Conciliação de cobrança incerta de agent run por provider de IA (root) | `platform_admin.py:122` (`.../agent-runs/{run_id}/billing-reconciliation`) | não | Nenhuma tela lista runs com custo incerto nem chama o endpoint. Adicionar aba em "Custos de IA" com lista de runs pendentes e ação de decisão. |
| 8 | 🔴 Dormente real | Bônus de quota de harmonização financeira (F14) — concessão manual root | `platform_admin.py:461` | não | Criar botão "Conceder bônus" na aba financeira do `WorkspaceAccount360`, junto ao card de status de quota já existente. |
| 9 | 🟠 Dormente real (parcial) | Estado por task individual do Celery Beat (25 tasks) | `celery_app.py` `beat_schedule`; único agregado é `GET /status` | parcial | `Status.tsx` só mostra contagem de workers ativos, sem granularidade por task. Falha silenciosa numa task específica não aparece em lugar nenhum. Criar aba "Workers" (root) com last-run/erro por task. |
| 10 | 🟡 Dormente real | SDR — status de qualificação e reprocessamento | `sdr.py:28,70` | não | Zero wrapper em `klm/api.ts`. Criar superfície mínima (seção em `LeadPage`) ou remover backend se SDR foi descontinuado — decisão de produto pendente. |
| 11 | 🟡 Dormente real | Migração de tickets técnicos legados (operação pontual de plataforma) | `platform_support.py:440` | não | Confirmado órfão (revalida achado da Onda 1). Se a migração já foi concluída, arquivar; se não, botão restrito a root no painel de SAC. |
| 12 | ⚪ Dormente real / decisão pendente | Stripe — checkout session + billing portal do workspace (F5) | `stripe_billing.py:32,47` | não | Zero referência a `stripe` em `klm/api.ts`. Caminho real de cobrança é Local Billing/Asaas. Decidir: remover o router ou documentar como backlog explícito. |
| 13 | ⚪ Dormente real / decisão pendente | Stripe — webhook de eventos de assinatura | `stripe_billing.py` | não | Dependente do item #12; sem checkout real o webhook nunca dispara. Mesma decisão de remover ou manter documentado. |
| — | 🔵 Backend-only por design (Onda 2-8) | Editor jurídico — artefatos (CRUD/gerar/submeter/checklist/export/reimportar) | `legal_editor.py:81-330` | não | Maior lacuna de valor entregue do lote (Onda 6 inteira); candidata a página própria "Editor de Peças" na próxima onda de UI. |
| — | 🔵 Backend-only por design | Demandas jurídicas — CRUD completo (criar/listar/obter/editar/transição de fase) | `legal_demands.py:65-176` | não | Base que destrava Caso 360, Estratégia, Editor e Protocolo. Página "Demandas Jurídicas" (lista+detalhe). |
| — | 🔵 Backend-only por design | Painel da Controladoria (agregado) | `legal_controladoria.py:348-370` | não | Dashboard principal que amarra regras de distribuição + SLA. |
| — | 🔵 Backend-only por design | SLA de prazos — editar, consultar, escalonamentos, recomputar | `controladoria.py:231-347` | não | Risco de perda de prazo processual sem tela de alerta visível. |
| — | 🔵 Backend-only por design | Regras de distribuição de demandas (Controladoria) | `legal_controladoria.py:73-176` | não | Tela "Controladoria" (regras de distribuição automática). |
| — | 🔵 Backend-only por design | DMS jurídico — upload, listagem, obter ativo, referências do documento | `legal_documents.py:86-303` | não | Sem tela de upload/listagem o DMS inteiro é inacessível ao usuário final. |
| — | 🔵 Backend-only por design | Pasta de protocolo — montar, obter, exportar .zip, registrar recibo | `protocol_folder.py:59-180` | não | Aba final "Protocolo" no fluxo Demanda → Editor → Protocolo. |
| — | 🔵 Backend-only por design | Lotes / operação massificada — criar, listar, itens, painel, processar, aprovar, rollback | `legal_batches.py:53-220` | não | Página "Lotes" é o modo de operação em massa da Onda 8. |
| — | 🔵 Backend-only por design | Importador universal de migração (job/prévia/importar/reconciliação/rollback) | `migration.py:43-212` | não | Wizard de migração (Astrea/ADVBOX/DataJuri) é onboarding crítico. |
| — | 🔵 Backend-only por design | Central de Exceções — fila, resolver, escalar | `exception_center.py:41-115` | não | Fila operacional diária de erros de automação/integração; merece item de menu próprio. |
| — | 🔵 Backend-only por design | Estratégia do caso — fatos, provas e teses | `legal_case_strategy.py:28-124` | não | Aba "Estratégia" na tela de Demanda. |
| — | 🔵 Backend-only por design | BI jurídico (operação/jurídico/IA) + capacidade da equipe | `legal_bi.py:33-90` | não | 4 dashboards podem entrar como abas no `PlatformDashboard`/`AdvancedReportsPanel` já existentes. |
| — | 🔵 Backend-only por design | Caso 360 canônico da demanda (distinto de `/matters/{id}/case-360`, que já tem UI) | `legal_demands.py:336-352` | não | Aba "Visão 360" reaproveitando layout de `ConversationCasePanel.tsx`. |
| — | 🔵 Backend-only por design | Atribuição e histórico de distribuição de demanda | `legal_controladoria.py:177-230` | não | Aba dentro da tela de Demanda ou do Painel de Controladoria. |
| — | 🔵 Backend-only por design | Partes da demanda (adicionar/listar) | `legal_demands.py:177-234` | não | Sub-seção "Partes" na tela de Demanda. |
| — | 🔵 Backend-only por design | Verificação de citações do artefato | `legal_demands.py:354-378` | não | Botão "Verificar citações" no Editor Jurídico. |
| — | 🔵 Backend-only por design | Revisão (review) de artefato | `legal_demands.py:380-400` | não | Botão "Enviar para revisão" no Editor Jurídico. |
| — | 🔵 Backend-only por design | Compartilhamento de documento — criar link, baixar, revogar, resgatar | `legal_documents.py:161-265` | não | Sub-ação "Compartilhar" dentro da tela de DMS. |
| — | 🔵 Backend-only por design | Semáforo do job de migração | `migration.py:161-175` | não | Indicador dentro da tela de status do job de migração. |
| — | 🔵 Backend-only por design | Divergências da migração — listar e resolver | `migration.py:255-290` | não | Lista de divergências dentro da tela de status do job — ação principal do usuário no fluxo. |
| — | 🔵 Backend-only por design (parcial) | Políticas avançadas de autonomia por agente (versão granular) | `klm_operations.py:1098-1192` | parcial | Anexar como "modo avançado" na tela de Agentes já existente (toggle simples já tem UI). |
| — | 🔵 Backend-only por design — manter assim por ora | Staging da migração + reprocessar registro individual | `migration.py:213-254` | não | Ferramenta de depuração fina; expor só se usuário pedir correção manual de linha. |
| — | 🔵 Backend-only por design — manter assim por ora | Quarentena e conflitos de sincronização de documentos | `legal_documents.py:146-221` | não | Cenário de exceção rara; expor só no primeiro caso real. |

---

## 2. Já resolvido nesta sessão (Onda 1 fechou)

Reconferido `docs/ONDA1_DORMENTES_BACKLOG.md` contra o código real (leitura apenas, nada alterado). Os itens abaixo saem da lista de pendências:

| Item | Status |
|---|---|
| Follow-up beat | ✅ Confirmado com código e testes |
| RBAC por projeção | ✅ Confirmado com código e testes |
| Autoria de êxito/contrato | ✅ Confirmado com código e testes |
| Feedback de conhecimento | ✅ Confirmado com código e testes |

**Correções de rotulagem (falso-positivo do backlog anterior, não eram dormentes):**

- **TiAgentPage (`/app/ti`)** — `App.tsx:332` (module=admin) → `frontend/src/klm/pages/TiAgentPage.tsx`. Já liga a backend real de SAC/tickets (`support_tickets.py`, `klm_operations.py:425`). Não é dormente; só estava mal rotulado como "agente de IA" quando na verdade é o painel de suporte técnico. Ação: corrigir só o rótulo no backlog, sem tocar em código.
- **Escalonamento de SAC fora de horário** (`GET/PUT /platform/support/escalation-settings`, `platform_support.py:126`) — já operacional via `SupportEscalationSettingsSection` em `PlatformSupportTicketsPanel.tsx`. Não é dormente, apesar de ter sido citado no escopo original.

---

## 3. Backend-only por design — Jurídico Ondas 2-8 (não é dormência acidental)

As 23 capacidades marcadas 🔵 na tabela do item 1 (~80 rotas: `legal_demands`, `legal_case_strategy`, `exception_center`, `legal_controladoria`, `legal_documents`, `legal_editor`, `protocol_folder`, `legal_batches`, `legal_bi`, `migration.py`) formam um bloco à parte, e por um motivo específico: **este backend foi construído nas Ondas 2-8 do roadmap jurídico, e a onda de UI correspondente ainda não rodou — não é código órfão por acidente, é sequenciamento**. O padrão de dependência é claro: Demandas (CRUD) destrava Partes, Caso 360, Estratégia e Editor; Editor destrava Protocolo; Controladoria (regras + SLA) destrava o Painel agregado; Migração (job/prévia/importar) destrava Semáforo, Staging e Divergências.

Duas exceções nomeadas já têm UI e não pertencem a este bloco: `/matters/{id}/case-360` e `/admin/automation-rules` — homônimos das rotas jurídicas, mas telas distintas já em produção.

**Por que isso não entra na contagem de "dormentes reais":** dormência real é capacidade pronta que ficou esquecida sem plano de exposição. Este bloco é o oposto — é capacidade pronta *esperando* a fase de UI que o próprio roadmap já previu (a auditoria da Onda 1 tratou justamente da fase de backend; a fase de UI é o próximo passo natural, não uma correção de bug). Tratar os 23 itens como "dormentes" ao lado do Stripe órfão ou do Meta CAPI sem endpoint de conexão equivaleria a confundir "atraso de roadmap" com "risco operacional não identificado" — e isso distorceria a priorização.

Dois subitens dentro do bloco (staging de migração, quarentena/conflitos de documento) são recomendados para **permanecer backend-only mesmo depois da onda de UI**, por serem ferramentas de depuração fina/cenário de exceção rara — expor só sob demanda real.

---

## 4. Veredito

**Dormentes reais restantes: 13** (itens 🔴/🟠/🟡/⚪ da tabela do item 1 — capacidade pronta, sem UI, sem plano de exposição anunciado).

**Backend-only por design (Ondas 2-8 jurídicas): 23 capacidades / ~80 rotas** — não contam como dormência, são trabalho de UI futuro já mapeado.

**Onda de superfície mínima recomendada para zerar os 13 dormentes reais**, em ordem de execução sugerida:

1. **Sprint A — Tracking de receita (risco mais alto):** `/connect/meta-capi` e endpoint de connection do Google Data Manager, com card de status em Integrações. Sem isso, a atribuição de conversão paga de Meta/Google não funciona em produção — é o item de maior risco financeiro silencioso do lote.
2. **Sprint B — Financeiro operacional:** painel único de recorrências (nativa + retainer pós-criação), botão de importação Asaas + seleção em lote no `ReconciliationPanel.tsx`, aba de custos de IA com runs de cobrança incerta, botão de bônus de quota no `WorkspaceAccount360`.
3. **Sprint C — Observabilidade e cauda longa:** aba "Workers" com granularidade por task do Celery Beat; decisão de produto sobre SDR (expor ou remover) e sobre Stripe checkout/webhook (remover router ou documentar como backlog formal); resolução do status da migração de tickets legados (arquivar se concluída).

Nenhum código foi alterado nesta auditoria — apenas leitura e consolidação.
