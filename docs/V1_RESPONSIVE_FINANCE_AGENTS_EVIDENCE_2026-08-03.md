# V1 responsiva, Financeiro 360 e agentes — evidência local

Data da fotografia: 05/08/2026.

Esta evidência não prova publicação. AWS, GitHub, Cloudflare, DNS e o site
público não foram alterados nesta certificação.

## Resultado vigente

| Gate | Resultado atual | Evidência |
|---|---:|---|
| Backend integral em PostgreSQL 17 — rodada 1 | 5.361/5.361, zero falha, erro ou skip | `.local/qa-evidence/postgresql-qa/cert1-*.xml` |
| Backend integral em PostgreSQL 17 — rodada 2 serial | 5.361/5.361, zero falha, erro ou skip | `cert2-final.xml` SHA-256 `156e95796d705b4bab246a782e6dfad164636c16c0829f76249bbdae686afcf3` |
| Backend integral em PostgreSQL 17 — rodada 3 serial | 5.361/5.361, zero falha, erro ou skip | `cert3-final.xml` SHA-256 `28ca37c74baa4153ba53faf50cf42fbac84acf38ba511cb5a6df5579afae2087` |
| Round-trip Alembic | base → head → base → head; cabeça única `no6h9d2f1a75` | `scripts/verify_postgresql_qa.sh` |
| QA PostgreSQL direcionado após correções | 57/57 | `.local/qa-evidence/postgresql-qa/sprint-d4-regression.xml` |
| Vitest paralelo | 194 arquivos, 1.100/1.100 | execução local em Node 24.18.0 |
| Vitest serial | 194 arquivos, 1.100/1.100 | execução local em Node 24.18.0 |
| Typecheck | PASS | `tsc --noEmit -p tsconfig.app.json` |
| Lint frontend | zero erro; 32 warnings não bloqueantes | ESLint + verificador de IDs internos |
| Bundles | `/app` e `/blackbox` PASS | `verify-production-bundle.mjs`; same-origin e sem dados dev/test |
| Responsividade e acessibilidade | 305/305 | bundles de produção, API controlada e Playwright serial |
| Financeiro Executivo | 3/3 | lookup paginado, drill-down e exportação contextual |
| Agentes, flows e humano | 2/2 | deployment, root_direct, checkpoint humano, validate e simulate |
| Verificador operacional local | 159 checks, concorrência 8, zero ação externa | `scripts/verify_klm_local.py` em banco limpo |
| Cobertura backend → frontend | 425 rotas, zero ausente | `.local/qa-evidence/blackbox-coverage-current.json` |
| Ruff incremental | zero violação nova | ratchet e verificação do verificador local |
| Integridade do diff | PASS | `git diff --check` |
| Checkout limpo do SHA funcional | PASS | clone limpo do commit `ae976cda9ed671d07b5e36826577d059f8e9df72` |
| Segredos no histórico e corpus alterado | zero achado | Gitleaks no intervalo Git e no corpus modificado |
| Upstreams OSS reproduzíveis | 5/5 materializados e verificados | `scripts/fetch_klm_upstreams.sh`; `scripts/verify_klm_upstreams.sh` |
| Infraestrutura como código | validação local aprovada, sem aplicação | commit local `612a673`; `cfn-lint cloudformation/*.yaml`; `scripts/verify_application_platform_contract.py` |

## Cobertura funcional comprovada nesta fotografia

- Financeiro dimensional: centro de custo → cliente → contrato → caso →
  lançamento, com relatórios e exportação contextual.
- Exportações financeiras privadas em PDF, XLSX e DOCX, com jobs,
  cancelamento, expiração e download autorizado.
- DRE, projeções, orçamento, valuation básico, tributação e alertas da reforma
  com revisão humana quando a decisão é fiscal ou externa.
- Agentes com definição, deployment, modelo, ferramentas, conhecimento,
  autonomia, orçamento e roteamento humano explícitos.
- Catálogo de nós tipado, validação e simulação antes da publicação.
- Roteamento humano por função e competência, escalada finita até o owner,
  checkpoint retomável e alerta in-app/telefone com estado `not_configured`
  honesto quando não há provider.
- Super Admin identificado em sessão `root_direct`, com MFA administrativo,
  auditoria e sem assumir a identidade do cliente.
- Superfícies novas do backend expostas no `/app` ou `/blackbox`; o auditor
  estrutural não encontrou rota administrativa interativa sem consumidor.
- Viewports 360, 393, 430, 768, 834, 1024, 1194 e desktop 1440, incluindo
  desktop-freeze fora do Financeiro e Axe nas superfícies críticas.

## Incidentes de certificação descartados com causa provada

1. A primeira matriz browser apontou para uma API de outro checkout na porta
   8010. A execução foi interrompida e não foi contada.
2. Os previews existentes apontavam para a porta 8013. Eles foram substituídos
   por previews da própria candidata, com proxy explícito para a API controlada.
3. Uma tentativa de build recebeu `VITE_API_URL` local e foi rejeitada pelo
   verificador. Os caches gerados foram isolados e os bundles foram refeitos
   same-origin; somente esses artefatos aprovados foram usados no E2E.
4. Tentativas concorrentes do backend foram descartadas porque compartilhavam
   estado local/Redis. As rodadas finais 2 e 3 foram executadas integralmente
   em série, com 5.361 testes e zero skip cada.

## Dependências e riscos conhecidos

- `react-router-dom` 7.18.2 recebe no `npm audit` o aviso
  `GHSA-qwww-vcr4-c8h2`, específico de React Server Components/Server Actions.
  Esta candidata é uma SPA Vite e não usa RSC nem Server Actions; portanto o
  vetor descrito não é alcançável nesta arquitetura. Não foi feito downgrade,
  pois versões anteriores reintroduzem advisories aplicáveis já corrigidos.
- As 26 advertências do backend são depreciações conhecidas; não houve falha,
  erro ou teste ignorado nas duas rodadas seriais finais.

## Gates externos ainda abertos antes de produção

- criar e testar a infraestrutura/staging AWS da candidata, incluindo banco,
  Redis, workers, storage privado, antimalware, backups, restore e rollback;
- provar secrets, alarmes, health checks e migrations no staging;
- somente então promover o mesmo digest e configurar
  `app.meuexecutivo.com`/`api.meuexecutivo.com`, preservando apex, `www` e o
  site público.

Esses gates são deliberadamente externos ao objetivo desta certificação local.
Nenhuma credencial, stack, domínio ou provedor foi alterado.

## Veredito local

**Candidata local funcionalmente aprovada e pronta para seguir ao gate de
staging AWS, mas ainda não autorizada como produção.** O código do SHA
`ae976cda9ed671d07b5e36826577d059f8e9df72` foi provado em checkout limpo; as
rodadas seriais finais passaram integralmente. A atualização deste documento é
somente evidência, não modifica o código certificado. Nenhuma integração
externa foi declarada ativa sem prova.
