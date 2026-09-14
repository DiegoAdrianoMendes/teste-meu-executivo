# Plano de Correção Total em Sprints — KLM Hub

> **Status:** `SUPERSEDED_NON_EXECUTABLE`. Este documento foi substituido por
> [`KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md`](./KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md).
> Ele permanece como evidencia da auditoria, mas nao autoriza remocao de funcoes,
> APIs externas, alteracoes online ou AWS.

Data: 19/07/2026. Branch auditado: `codex/saas-foundation`. Estado historico:
**aguardava aprovacao; nada foi executado por este plano.**

Origem: auditoria QA end-to-end completa de 19/07/2026 (esta sessão), com 6 frentes de revisão independentes: performance de backend, custo de IA, frontend, infraestrutura, segurança e contrato de API backend × frontend.

---

## 0. Estado atual comprovado (baseline)

Evidência reproduzível, tudo executado em 19/07/2026 na stack local:

| Suíte | Resultado |
|---|---|
| Backend `pytest` | 1.538 passed, 11 skipped, 0 falhas (4m31s) |
| Frontend Vitest | 176 passed (22 arquivos) |
| `tsc --noEmit` | limpo |
| Build Vite | ok, code splitting por rota funcionando |
| `verify_klm_local.py` | 124 checks PASS, `externalActionsExecuted=0` |
| Playwright E2E | 205 passed (11,9 min), desktop 1440 + mobile 393 |
| QA manual navegador | 10 páginas navegadas, zero erros de console, dark mode, mobile ok |
| Contrato FE→BE | **zero chamadas quebradas** (253 paths do FE, todos existem no BE) |

O sistema funciona. Este plano não é conserto de coisa quebrada: é o caminho de "funciona local" para "SaaS de produção sustentável, barato e rápido".

## 1. Meta global (definição de "perfeito")

O plano termina quando TODOS os critérios abaixo forem verdadeiros, comprovados por evidência:

1. **Custo de IA governado**: 100% das chamadas de modelo passam pelo gateway, com catálogo de roteamento versionado no repo (barato por padrão), telemetria de custo persistida por workspace para todas as chamadas, e budget diário com alerta.
2. **Latência**: p95 dos endpoints quentes (login, inbox, dashboard, conversas) abaixo de 300 ms com dados realistas; Home faz no máximo 5 requests para pintar; zero preflight CORS em produção (same-origin).
3. **Segurança de borda**: rate limiting em todas as rotas de autenticação, sem oráculos de timing, webhooks todos com HMAC + replay protection, template rendering com allowlist.
4. **Contrato de API limpo**: zero rotas órfãs não documentadas, zero serviços mortos, uma única superfície por operação (sem duplicações legadas).
5. **Um único alvo de deploy**, com CI que builda uma vez, imagens enxutas (multi-stage + .dockerignore), limites de recurso, rotação de logs, backup testado por restore e observabilidade mínima (erros + uptime + custo).
6. **Suítes 100% verdes em todos os fechamentos de sprint** (pytest + Vitest + tsc + build + Playwright + verify_klm_local), padrão já estabelecido no baseline.

Regra de condução: sprints com checkpoint de aprovação entre cada um; revisão em 2 passes (executor + revisor independente) antes de fechar cada sprint; nenhum sprint fecha com teste vermelho ou placeholder.

---

## Sprint 0 — Fundação e decisões (meio dia + 1 decisão do dono)

Objetivo: destravar o trabalho com o repo em estado seguro.

1. Commitar os ~510 arquivos modificados/untracked do branch (estado atual auditado e verde). Sem commit, qualquer automação de auditoria em worktree é arriscada.
2. Remover diretório vazio `{}}` da raiz.
3. **DECISÃO DO DONO (bloqueante para Sprint 6)**: alvo de produção único. Opção A: VPS Hostinger com compose + Traefik (mais barato, já real). Opção B: AWS ECS/RDS (mais caro, mais elástico). O que não for escolhido será desligado, hoje os dois coexistem e o espelho AWS custa US$ 60-100+/mês parado.
4. Registrar no repo o baseline deste plano como referência de regressão.

Critério de saída: `git status` limpo, decisão de alvo registrada.

## Sprint 1 — Segurança de borda (1-2 dias)

Objetivo: nenhum vetor remoto sem fricção contra autenticação e webhooks.

1. Rate limiting por IP + conta em `/auth/login`, `/auth/forgot-password`, `/auth/reset-password` (Redis, padrão já existente em `public_api_service._enforce_rate_limit`), com backoff e lockout progressivo.
2. Eliminar oráculo de timing no login: `verify_password` contra hash dummy quando usuário não existe (`app/services/auth_service.py:185`).
3. Eliminar oráculo de timing no forgot-password: envio de email via worker fora do caminho da resposta (`app/api/v1/endpoints/auth.py:260`).
4. Webhook ZapSign: HMAC do corpo + janela de replay, validação antes de qualquer trabalho (`app/api/v1/endpoints/webhooks.py:345`).
5. `message_template_service`: trocar `str.format_map` por renderer com allowlist de chaves (`app/services/message_template_service.py:198`).
6. Hardening menor: subchaves derivadas por propósito da `SECRET_KEY` (HKDF), claims `aud`/`iss` nos JWT, normalizar email com `.lower()` no login, loopback check no `OllamaProvider`.
7. Migrar `python-jose` (semi-abandonada) para `PyJWT` e eliminar `datetime.utcnow()` deprecado.

Critério de saída: testes novos cobrindo rate limit e timing constante; suítes 100% verdes.

## Sprint 2 — Latência de backend (2-3 dias)

Objetivo: caminho quente eficiente antes de qualquer carga real.

1. Cliente Redis singleton em `token_store.is_token_revoked` (hoje abre e fecha conexão a CADA request autenticado, `app/core/token_store.py:291`).
2. bcrypt via `asyncio.to_thread` em `verify_password`/`hash_password` (`app/core/security.py:252`), hoje congela o event loop a cada login.
3. Corrigir N+1 do inbox WhatsApp: última mensagem + contagem de não lidas em query única com `DISTINCT ON`/window function (`app/services/whatsapp/message_service.py:219`, hoje ~101 queries por página).
4. Índices compostos: `Matter (workspace_id, column_id) WHERE deleted_at IS NULL`, `Matter (workspace_id, created_at)`, `WhatsAppMessage (contact_id, direction, status)`, `WhatsAppContact (workspace_id, updated_at)`.
5. Dashboard: agregações com `FILTER` em 1-2 queries + cache Redis TTL curto (`app/api/v1/endpoints/reports.py:57`).
6. `KEYS` → `SCAN` na invalidação de cache (`app/core/cache.py:97`).
7. Celery beat: descoberta de "workspaces com trabalho devido" antes do fan-out (hoje enfileira para todos os tenants por minuto: `whatsapp_campaign_tasks.py:66`, `notification_tasks.py:63`, `flow_tasks.py:1711`).
8. Tirar commit/refresh por item do caminho de leitura de `list_flows` (`app/services/flow_service.py:1113`).
9. Endpoint agregador para a Home (hoje 20 requests) e, em produção, SPA + API sob o mesmo origin (elimina 20 preflights OPTIONS).

Critério de saída: medição antes/depois dos endpoints quentes registrada no doc do sprint; suítes verdes.

## Sprint 3 — Custo de IA (2-3 dias, ANTES de ligar APIs de modelo)

Objetivo: quando `KLM_MODEL_APIS_ENABLED=true` for ligado, o custo já nasce governado.

1. **Catálogo de roteamento versionado no repo** (hoje `KLM_AI_ROUTE_CATALOG_JSON` vem vazio): escada SIMPLE (mini/lite/haiku-class: sdr, crm, secretaria, qualificação, insights) → MEDIUM (flash/sonnet-class: copiloto, closer, paralegal) → COMPLEX (juridico, multimodal) → PREMIUM (somente `peticao`/`klm_drafter`). Teste que garante SIMPLE ≠ PREMIUM.
2. **Telemetria universal**: `TelemetrySink` que persiste tokens/custo por workspace injetado no `build_model_gateway`, cobrindo transcrição, insights, meta_ai e stubs (hoje só o caminho de AgentRun grava em `PlatformTokenUsage`).
3. **SDR**: janela de histórico (12-16 trocas + resumo rolante) e leitura única por turno (hoje reenvia o histórico completo do WhatsApp 2x por turno, `app/agents/stubs/klm_sdr.py:312`).
4. **Transcrição dentro do gateway** com task_class própria, `max_output_tokens`, workspace e budget (hoje chama Vertex cru sem teto, `app/services/transcription_service.py:65`).
5. `max_tokens` explícito em todos os chamadores de texto livre (insights ~500, SDR resposta, etc.).
6. Cache opt-in para chamadas determinísticas não sensíveis + `cache_control: ephemeral` no adapter Anthropic (prompt caching hoje nunca dispara).
7. Fallback do `meta_ai_service`: não recair de tier quando a chamada já iniciou cobrança (`app/services/meta_ai_service.py:208`).
8. `simulate_health_intake`: reusar contexto único na sequência de 4 agentes e condicionar o passo COMPLEX a gatilho barato.
9. Streaming nas saídas long-form do drafter (latência percebida).
10. Alerta de budget: notificação quando workspace atinge 80% do teto diário.

Critério de saída: relatório de custo simulado por task_class; nenhuma chamada de modelo fora do gateway (teste de guarda); suítes verdes.

## Sprint 4 — Contrato de API e código morto (1-2 dias)

Objetivo: superfície de API igual ao produto, sem sombras.

Base: auditoria de 19/07 encontrou 411 rotas no backend, 253 consumidas pelo FE, zero quebradas, ~30 órfãs reais, ~17 de uso externo legítimo, 2 serviços mortos.

1. Remover ou wirar no FE (decidir caso a caso) as órfãs reais: módulo notifications completo (4 rotas), tasks PATCH/toggle, `matters` reorder/notes, `flows` scheduled-messages DELETE e revisions, `whatsapp/connections` CRUD, `documents` DELETE, `reports/kpi-aggregate`, `platform` mrr/token-usage/aws-costs, `follow-ups suppress`.
2. Manter e **documentar como internal/ops-only**: rotas de reconciliação/recuperação (`external-action/reconcile`, `billing-reconciliation`, `scheduled-messages/reconcile`) e auditoria (`matters/*/audit`, `field-changes`).
3. Eliminar duplicações: aposentar wrapper legado `POST /whatsapp/connect` em favor do canônico; consolidar `/users/*` × `/admin/*` numa superfície única; reduzir as 6 formas de mover estágio de matter e as 5 superfícies de KPI para uma canônica por operação.
4. Remover serviços mortos `google_ads_service.py` e `meta_ads_service.py` (zero imports fora de teste) ou registrar decisão de completar a integração.
5. Gate anti-regressão: script/teste que gera o inventário de rotas do app e compara com um manifesto versionado de consumidores (FE, webhook, ops-only); rota nova sem classificação quebra o CI.

Critério de saída: manifesto de contrato versionado; suítes verdes (ajustar testes das rotas removidas).

## Sprint 5 — Frontend (1-2 dias)

Objetivo: runtime enxuto e experiência instantânea.

1. `useMemo` no value do `AppContext` + separar state de actions (hoje objeto literal de 30+ campos re-renderiza a app inteira, `frontend/src/store/app-context.tsx:941`).
2. Converter os 5,9 MB de GIFs de `public/site-media` para mp4/webm via o branch de vídeo do `PublicMediaFrame` (reduz ~90%).
3. Debounce (500ms-1s) e refresh seletivo por evento no handler de WebSocket (hoje 4 refreshes por evento).
4. Isolar o relógio de cadência num subcomponente (timer de 250ms re-renderiza a ConversationsPage inteira de 1.265 linhas).
5. Backoff progressivo no polling de status OSS degradado (2s → 5s → 15s).
6. Virtualização (`@tanstack/react-virtual`) em conversas, leads e colunas de funil.
7. `manualChunks` no Vite (react, radix, react-query, recharts) para cache de vendor estável entre deploys.
8. Timeout com `AbortController` em `apiFetch`/`api-client`; retry global inteligente (não repetir 4xx); remover `refetchIntervalInBackground` da Agenda.

Critério de saída: bundle report antes/depois; suítes verdes incluindo Playwright completo.

## Sprint 6 — Infra e deploy de produção (2-3 dias, depende da decisão do Sprint 0)

Objetivo: um alvo, imagens enxutas, custo previsível.

1. `.dockerignore` raiz (`.venv`, `.local`, `.vendor`, `frontend`, `tests`, `.git`, caches) e `frontend/.dockerignore`: contexto cai de ~3 GB para dezenas de MB.
2. Multi-stage no `Dockerfile` backend (modelo: `Dockerfile.frontend` já é multi-stage); sem gcc/libpq-dev na imagem final.
3. Desligar o caminho de deploy não escolhido: ou o espelho AWS (ECS + RDS + ElastiCache + ALB, maior economia recorrente), ou o compose da VPS + systemd. Aposentar também a dualidade systemd × compose na VPS (hoje dois runtimes disputam a porta).
4. CI builda uma vez e publica; VPS/ECS só faz pull (hoje builda na VPS via SSH e rebuilda em CodeBuild).
5. Limites de recurso (`cpus`/`mem_limit`) e rotação de log (`max-size`/`max-file`) em todos os composes.
6. Sidecars OSS: consolidar 4 Postgres + 3 Redis em 1 Postgres pgvector (bancos lógicos) + 1 Redis (DBs numerados); MongoDB fica só para Rocket.Chat. De ~21 para ~14 containers; documentar "suba só o profile em uso".
7. Nginx: gzip/brotli completo, http2, proxy same-origin para a API (fecha o item de preflight do Sprint 2).
8. ECR lifecycle policy; consolidar buckets `builds` × `builds-v2`; Celery `-c` proporcional ao vCPU; avaliar beat embutido.

Critério de saída: deploy de ponta a ponta no alvo único com smoke test; custo mensal estimado documentado.

## Sprint 7 — Operação sustentável de SaaS (2-3 dias + rotina contínua)

Objetivo: capacidade de operar clientes pagantes sem sustos.

1. **Observabilidade**: error tracking já existe internamente; adicionar uptime check externo, alerta de erro novo, dashboard de custo IA por workspace (dados do Sprint 3) e alerta de budget.
2. **Backups**: dump automático diário do Postgres com retenção, e **teste de restore mensal** (backup sem restore testado não é backup).
3. **Migrations com gate**: alembic upgrade no deploy com passo de rollback documentado.
4. **Staging**: ambiente de homologação mínimo (pode ser profile no mesmo host) onde toda release roda a suíte E2E antes de produção.
5. **Load test**: k6/locust nos endpoints quentes com dados realistas; fixa o baseline do p95 da meta global.
6. **Runbook**: 1 página por incidente comum (API caiu, worker parado, disco cheio, webhook rejeitando, budget IA estourado).
7. **Rotina mensal**: revisar custo real (infra + IA por workspace), atualizar dependências com CVE, rodar restore test, revisar `INFRA_AUDIT.md`.
8. **Go-live gate**: checklist final: suítes verdes, load test ok, backup/restore ok, alertas disparando, runbook completo, gates de modelo/canais ligados conscientemente um a um.

Critério de saída: checklist de go-live 100% verde. Aqui o sistema está "perfeito" segundo a meta global.

---

## Sequência e dependências

```
S0 (fundação + decisão de alvo)
 ├─ S1 Segurança ──┐
 ├─ S2 Latência ───┼─ S4 Contrato ─ S5 Frontend ─ S6 Infra ─ S7 Operação ─ GO-LIVE
 └─ S3 Custo IA ───┘
```

S1, S2 e S3 são independentes entre si e podem ser paralelizados. S6 depende da decisão do S0. S7 fecha o ciclo. Estimativa total: **10 a 16 dias úteis** de execução com checkpoints de aprovação entre sprints.

## Regras de condução (invariantes)

- Nenhum sprint fecha com teste vermelho, placeholder ou "TODO depois".
- Toda mudança passa por revisão independente em 2 passes antes do checkpoint.
- Todo fechamento de sprint reroda a bateria completa do baseline (pytest + Vitest + tsc + build + Playwright + verify_klm_local) e registra a evidência no doc do sprint.
- Gates de produção (`KLM_MODEL_APIS_ENABLED`, canais externos, AWS) permanecem desligados até o go-live gate do S7, ligados um a um com aprovação explícita.
