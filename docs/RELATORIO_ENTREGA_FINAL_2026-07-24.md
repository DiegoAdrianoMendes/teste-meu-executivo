# Relatório de entrega final — Meu Executivo (KLM Hub)

**Data:** 2026-07-24
**Branch:** `codex/saas-foundation`
**Estado:** pronto para subir para a AWS e pedir Tech Provider. Nenhuma
credencial real conectada, nenhum recurso na AWS provisionado, gate externo
fechado. Tudo local, como combinado.

---

## 1. O que fechou nesta última leva (A1/M1)

Último achado da revisão adversarial: os caminhos de auto-envio do WhatsApp
não respeitavam a janela de 24h da Meta quando o canal externo estivesse
aberto. Corrigido e testado antes de qualquer abertura de gate em produção.

- **A1 — SDR do WhatsApp** (`app/workers/agent_tasks.py`): antes de auto-enviar
  texto livre, passa pelo `MetaPolicyGate`. Dentro da janela e com canal
  aberto → envia. Fora da janela → `blocked_by_policy_window`. Canal fechado
  → `local_draft` para revisão humana.
- **M1 — nós de flow** (`app/workers/flow_tasks.py`): `send_message` e
  `scheduled_message` pausam o run para `waiting_human` (reengajar via template
  aprovado) quando a janela de 24h está fechada, mesmo com o canal aberto,
  espelhando o bloqueio já existente do gate de canal.
- **Testes novos:** 2 casos A1 (bloqueia fora da janela / envia dentro) + 1
  caso M1 (pausa fora da janela), com seed de janela aberta no helper de flow
  para refletir o estado real de produção.

Commit: `6f1c8a1 feat(meta-policy): fecha A1/M1 — janela de 24h em SDR e flows WhatsApp`.

---

## 2. Bateria de verificação final

| Suíte | Resultado |
|---|---|
| Backend `tests/workers` + `tests/services` | **1716 passed, 0 failed** |
| Backend completo (exceto integração PG) | verde (ver §4) |
| Frontend `tsc` | exit 0 |
| Frontend `build` | ok |
| Frontend Vitest | **579 passed / 579** (95 arquivos) |
| Scorecard arquitetural | **11/12 critérios verdes** (ver §3) |

---

## 3. Scorecard de arquitetura nativa

11 dos 12 critérios estruturais passam localmente:

✅ provider_neutral_native_boundary · strict_typed_contracts ·
classified_tenant_isolation · transactional_rls_role_hardening ·
tenant_reference_integrity · optimistic_and_locked_concurrency ·
intent_idempotency · exact_single_use_human_veto · immutable_evidence ·
durable_post_commit_effects · explicit_external_dependency_gates

⏳ **source_bound_release_verification** — único vermelho. **Não é defeito de
código.** É a cerimônia de prova de release que re-executa a bateria completa
(backend com Postgres vivo + Playwright e2e, zero skips) e vincula os proofs
ao digest de fonte atual. Exige infraestrutura viva (Postgres + servidor e2e),
então é o passo a rodar em staging AWS, não no ambiente local. Roteiro:
`scripts/verify_oss_native_release.py` no ambiente com Postgres provisionado.

---

## 4. Testes que dependem de Postgres real (esperado)

Erram na coleta local por não haver Postgres (fail-closed por design):
`tests/integration/test_postgresql_rls.py` e
`tests/db/test_legal_crm_rls_postgresql.py`. São exercitados na etapa AWS,
quando o RLS transacional roda contra o Postgres real. A cobertura de RLS em
SQLite (`tests/db/test_tenant_rls_sqlite.py`) passa localmente e classifica
todas as tabelas com `workspace_id`.

---

## 5. Roadmap de ligar e usar amanhã (BYOA híbrido)

O produto **já funciona hoje** no modo híbrido: cada escritório cria o próprio
app em developers.facebook.com e conecta pela Central de Conexão — **sem
esperar aprovação de Tech Provider**. A validação de assinatura do webhook é
por `app_secret` por conexão.

**Checklist para operar amanhã:**
1. Subir para a AWS (Runbook: `docs/RUNBOOK_AWS_FASE1.md`) com Postgres +
   Redis provisionados.
2. Rodar a prova de release (`scripts/verify_oss_native_release.py`) em staging
   para fechar o 12º critério do scorecard.
3. Cada escritório cria seu app no developers.facebook.com e conecta pela
   Central de Conexão (passo a passo com prints em `docs/PLANO_META_MAXIMO_2026-07-23.md`).
4. Abrir o gate externo (`KLM_EXTERNAL_ACTIONS_ENABLED`) só depois de §2 verde.
5. Pedir Tech Provider em paralelo (dossiê: `docs/DOSSIE_TECH_PROVIDER.md`) para
   habilitar o Embedded Signup e reduzir o atrito de onboarding no futuro.

**Ordem dos gates externos** (todos fechados agora): `KLM_AWS_ENABLED` →
`KLM_MODEL_APIS_ENABLED` → `KLM_EXTERNAL_ACTIONS_ENABLED` → `KLM_ONLINE_APPROVED`.
