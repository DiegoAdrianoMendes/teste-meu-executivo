# Audit Remediation Matrix

Data de atualização: 2026-04-04

## Escopo

Esta matriz consolida a remediação dos findings críticos levantados na auditoria de segurança, multi-tenancy e aderência frontend/backend do KLM Legal Hub.

## P1

| Finding | Área | Correção aplicada | Teste / validação | Status |
|---|---|---|---|---|
| `P1-01` Aprovações públicas e cross-tenant | `app/api/v1/endpoints/approvals.py`, `app/services/approval_service.py` | Rotas agora exigem autenticação, workspace ativo e permissão; consultas filtram por `workspace_id` | `tests/api/test_security.py::test_public_endpoints_require_authentication` e `test_cross_tenant_task_document_and_approval_access_is_blocked` | Corrigido |
| `P1-02` Tasks/documents públicos | `app/api/v1/endpoints/tasks.py`, `app/api/v1/endpoints/documents.py`, services correspondentes | Rotas protegidas com JWT, workspace e RBAC; services validam pertencimento ao tenant | `tests/api/test_security.py::test_public_endpoints_require_authentication` e `test_cross_tenant_task_document_and_approval_access_is_blocked` | Corrigido |
| `P1-03` Setup aberto irrestrito | `app/api/v1/endpoints/workspaces.py`, `app/schemas/workspace.py`, `app/core/config.py` | `setup_token`, limitação por IP em Redis, logging de criação e exigência do token em produção | `tests/api/test_security.py::test_workspace_setup_requires_invite_token_in_production` | Corrigido |
| `P1-04` OpenAPI público em produção | `app/main.py`, `app/services/integration_service.py`, `app/schemas/integration.py` | `openapi.json` desabilitado por padrão em produção; contratos do admin refletem opcionalidade | Validação por configuração + build/testes | Corrigido |
| `P1-05` Rejeição HITL era stub | `app/workers/event_bus.py`, `app/services/agent_runtime_service.py` | `dispatch_approval_resolved()` agora reencaminha execução com contexto de rejeição/ajuste | Validação estática + integração com fila/worker pendente de smoke completo | Parcialmente corrigido |
| `P1-06` SPA 100% mockada | `frontend/src/services/http.ts`, `frontend/src/services/api.ts`, `frontend/src/services/*`, `frontend/src/store/app-context.tsx`, `frontend/src/pages/Admin.tsx` | Board, approvals, reports, admin, finance e integrações passaram a usar API real; superfícies sem backend deixaram de fingir persistência | `npm run build` + revisão manual do fluxo | Parcialmente corrigido |

## P2

| Finding | Área | Correção aplicada | Teste / validação | Status |
|---|---|---|---|---|
| `P2-01` `SECRET_KEY` insegura | `app/core/config.py` | Startup de produção falha com `change-me-in-production` | Validação estática; ainda falta teste automatizado específico | Corrigido |
| `P2-02` CORS wildcard | `app/main.py`, `app/core/config.py` | CORS usa `settings.CORS_ORIGINS`; produção rejeita configuração aberta | `pytest` indireto + revisão estática | Corrigido |
| `P2-03` WhatsApp mutando settings globais | `app/services/whatsapp_service.py` | Configuração de WhatsApp passou a ser lida do `workspace.settings` | Validação estática; smoke test de WhatsApp ainda pendente | Parcialmente corrigido |
| `P2-04` Credencial Vertex em disco | `app/ai/vertex_gemini.py` | Credenciais agora são carregadas em memória via `service_account_info` | `pytest` + import/load em runtime | Corrigido |
| `P2-05` HMAC opcional fora de produção | `app/api/v1/endpoints/webhooks.py` | Validação de assinatura roda sempre que segredo configurado | Revisão estática | Corrigido |
| `P2-06` Lookup de matter por `case_number` sem tenant | `app/api/v1/endpoints/webhooks.py` | Lookup agora exige `workspace_slug` e filtra por `workspace_id` | Revisão estática | Corrigido |
| `P2-08` Celery sem retry/timeouts | `app/workers/celery_app.py` | `KLMResilientTask` aplica `autoretry_for` restrito a `ConnectionError`/`TimeoutError`, backoff com jitter e limites padrão de 55/60s; tasks com política específica continuam sobrescrevendo os defaults | `tests/workers/test_agent_tasks.py` + smoke de import do worker | Corrigido |
| `P2-10` JWT longo e sem refresh/revogação | `app/core/security.py`, `app/core/deps.py`, `app/core/token_store.py`, `app/api/v1/endpoints/auth.py`, frontend auth/http | Access token curto, refresh token, revogação em Redis e refresh automático no client | `pytest` geral + build; ainda falta teste automatizado dedicado ao refresh | Parcialmente corrigido |

## P3

| Finding | Área | Correção aplicada | Teste / validação | Status |
|---|---|---|---|---|
| `P3-01` RBAC granular pouco aplicado | `app/core/rbac.py`, endpoints de matters/tasks/documents/reports/approvals | Novas permissões `create/edit/delete/approve/export/send_message`; rotas principais agora usam `require_permission` | `pytest` geral + revisão estática | Parcialmente corrigido |
| `P3-02` `changes_requested` faltando no frontend | `frontend/src/types/index.ts` e fluxo de approvals | Tipo e ações alinhados com backend | `npm run build` | Corrigido |
| `P3-05` agregações e filtros ainda incompletos | `app/services/matter_service.py`, `app/services/reports_service.py` | Parte dos relatórios já é tenant-aware real; filtros avançados ainda podem ser aprofundados | `pytest` + build | Parcialmente corrigido |

## Evidências executadas nesta rodada

- `python3 -m pytest -q` -> `11 passed`
- `npm run build` -> sucesso

## Pendências reais após esta remediação

1. Adicionar testes específicos de refresh/revogação de JWT.
2. ~~Endurecer tasks Celery com retry/backoff/timeouts.~~ Corrigido com política transversal e testes dedicados.
3. Completar smoke test do fluxo HITL rejeitado em ambiente com worker e broker reais.
4. Expandir alinhamento dos filtros avançados entre API paginada e SPA.
5. Finalizar conectores provider-specific e fluxos externos reais.
