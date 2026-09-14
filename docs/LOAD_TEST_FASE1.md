# Load test — Fase 1 (endpoints quentes)

> Sprint: Operação de produção (load test + drill de restore + observabilidade),
> worktree `klm-wt-ops-prod`, branch `sprint/ops-prod`. Executado em 2026-07-23
> contra o backend local (`scripts/run_local.py`, SQLite), **nunca** contra
> nuvem — nenhum comando `aws`/`terraform` foi rodado.

## Ferramenta escolhida

`k6` e `locust` **não estão instalados** neste ambiente de execução
(`which k6 locust` → not found, verificado em 2026-07-23). Em vez de instalar
uma dependência nova sem aprovação, este sprint entrega os dois caminhos:

1. **`tests/load/k6_hot_endpoints.js`** — script k6 pronto, para o
   proprietário rodar assim que instalar o k6 (`brew install k6`). Cobre os
   mesmos 4 endpoints, com thresholds de p95 < 300ms e `http_req_failed <
   1%` configurados nativamente no k6.
2. **`scripts/load/run_hot_endpoints.py`** — fallback em Python puro
   (asyncio + httpx, já presente no `.venv`), **executado de fato nesta
   sessão** contra o backend local. É o resultado abaixo.

Ambos medem os mesmos 4 endpoints "quentes":

| Endpoint | Rota real |
|---|---|
| Login | `POST /api/v1/auth/login` |
| Inbox / conversas | `GET /api/v1/klm/omnichannel/events` |
| Dashboard | `GET /api/v1/klm/dashboard` |
| Tracking | `GET /api/v1/klm/tracking/summary` |

## Como reproduzir

```bash
# 1) Subir o backend local em porta livre (NUNCA a 8010 se o repo principal
#    já estiver rodando lá — confira com `lsof -nP -iTCP:8010 -sTCP:LISTEN`
#    antes; nesta sessão o 8010 já estava ocupado pelo repo principal e o
#    load test rodou na 8011, isolado na worktree).
.venv/bin/python scripts/run_local.py --host 127.0.0.1 --port 8011 &

# 2) Semear o usuário local padrão + senha de teste conhecida (guardado,
#    só roda em runtime local_simulation)
.venv/bin/python scripts/seed.py
.venv/bin/python scripts/load/set_local_test_password.py

# 3) Semear identidades adicionais para não esbarrar no rate limiter de
#    login por identidade (5 tentativas/15min, ver
#    app/services/auth_rate_limit_service.py)
.venv/bin/python scripts/load/seed_load_test_users.py --count 25

# 4) Rodar o load test
.venv/bin/python scripts/load/run_hot_endpoints.py \
  --base-url http://127.0.0.1:8011 \
  --concurrency 10 \
  --iterations-per-worker 30
```

O rate limiter de login também tem um teto **por rede** (30 tentativas/15min,
mesmo IP) — em ambiente local isso é atingido rápido por causa dos próprios
testes manuais/repetidos. Para resetar o bucket local entre execuções (só em
runtime local_simulation, nunca em produção):

```bash
sqlite3 .local/klm-hub.db "delete from auth_rate_limit_buckets where scope='login';"
```

## Resultado real medido (2026-07-23, SQLite local, processo único uvicorn)

Três níveis de concorrência, 30 requisições GET por worker (login 1x por
worker, reaproveitando o token — como um usuário real faria):

| Concorrência | login p50/p95 | inbox p50/p95 | dashboard p50/p95 | tracking p50/p95 | erros |
|---|---|---|---|---|---|
| 5  | 357 / 358 ms  | 56 / 94 ms   | 46 / 63 ms   | 61 / 93 ms   | 0 |
| 10 | 546 / 594 ms  | 125 / 171 ms | 110 / 134 ms | 136 / 195 ms | 0 |
| 20 | 795 / 1023 ms | 354 / 573 ms | 186 / 305 ms | 344 / 462 ms | 0 |

Relatórios JSON completos (p50/p95/p99/max, contagem, status codes) ficam em
`.local/qa-evidence/ops-prod/` (não versionado — evidência local).

### Leitura do resultado

- **`inbox`, `dashboard`, `tracking` cumprem a meta de p95 < 300ms até
  concorrência 10.** Em concorrência 20 (acima do volume esperado da Fase 1,
  1–20 escritórios) o `inbox` e o `tracking` já excedem levemente — mas isso é
  medido contra **SQLite + processo único** (`uvicorn` sem workers), que é o
  runtime de desenvolvimento, não o de produção. `docker-compose.prod.yml` já
  roda `api`/`worker` como serviços separados contra Postgres gerenciado
  (RDS na Fase 1, ver `docs/RUNBOOK_AWS_FASE1.md`); a expectativa é performance
  bem melhor lá (índices Postgres + connection pool vs. SQLite single-writer).
- **`login` é o único que não bate a meta em nenhum nível de concorrência**, e
  isso é **por desenho, não é bug de performance**: o hash de senha usa
  `bcrypt` (custo fixo ≈ 300-350ms de CPU por verificação, decisão de
  segurança contra força bruta) mais um piso de temporização uniforme de
  0,35s (`enforce_uniform_timing`, mitigação de user enumeration por timing).
  Um p95 de 300ms para login bcrypt+piso-uniforme não é uma meta realista sem
  trocar o desenho de segurança — **recomendação: manter a meta de p95 <
  300ms para os GETs quentes (inbox/dashboard/tracking) e definir uma meta
  separada e maior para login** (ex.: p95 < 1.200ms), refletindo o custo de
  segurança intencional do endpoint.
- **Zero erros HTTP** em todas as execuções (fora os 429 esperados do rate
  limiter quando testado incorretamente contra uma única identidade — ver
  seção anterior).

## Pendência

- Rodar o mesmo teste (`tests/load/k6_hot_endpoints.js` ou
  `scripts/load/run_hot_endpoints.py`) contra a stack `docker-compose.prod.yml`
  com Postgres real, para confirmar que a meta de p95 < 300ms nos GETs
  quentes se sustenta ou melhora com múltiplos workers + Postgres — não
  rodado nesta sessão (não há Postgres de "produção" local rodando a stack
  completa via Docker Compose; só o Postgres efêmero de QA/drill).
- Instalar `k6` (`brew install k6`) e rodar `tests/load/k6_hot_endpoints.js`
  para obter as métricas nativas de k6 (incluindo distribuição de erros por
  cenário de rampa) como segunda fonte de verdade.
