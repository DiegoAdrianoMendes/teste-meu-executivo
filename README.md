# KLM Hub

Plataforma pessoal de operação jurídica, comercial e financeira da KLM Advogados, com SPA, CRM, omnichannel, automações, áreas plug and play e agentes governados.

## Fase atual: pacote local para aprovação

O contrato canônico desta fase está em [docs/KLM_HUB_PLANO_E_APROVACAO_LOCAL.md](docs/KLM_HUB_PLANO_E_APROVACAO_LOCAL.md).

```bash
# API local, SQLite e efeitos externos bloqueados
python3 scripts/run_local.py --port 8010

# Em outro terminal, validar API, gates e concorrência local
python3 scripts/verify_klm_local.py --base-url http://127.0.0.1:8010

# SPA local, em outro terminal
cd frontend
npm run dev -- --host 127.0.0.1 --port 4173
```

- SPA: `http://127.0.0.1:4173/app`
- API: `http://127.0.0.1:8010`
- Swagger: `http://127.0.0.1:8010/docs`

Nesta fase, não use credenciais reais, não habilite provedores e não execute deploy. O lançamento foi desenhado para usar APIs de modelos; o simulador determinístico existe somente para desenvolvimento e testes. Modelos, canais externos e AWS têm gates independentes e continuam desligados.

Antes de aprovar a próxima fase, a evidência reproduzível inclui `pytest`, `Vitest`, `tsc`, lint, build e Playwright. Os comandos e resultados canônicos estão em `docs/KLM_HUB_PLANO_E_APROVACAO_LOCAL.md`.

Baseline R8: 684 testes backend, 80 frontend, 71 E2E e 10 provas PostgreSQL RLS/concorrência aprovados; 77 checks HTTP executados com `externalActionsExecuted=0`.

Construído com **React/Vite**, **FastAPI**, **PostgreSQL**, **SQLAlchemy 2.0 (Async)**, **Redis**, **WebSockets** e sidecars preservados de **Chatwoot** e **Rocket.Chat**.

---

## Arquitetura

```
klm-legal-hub-backend/
├── app/
│   ├── api/v1/endpoints/   # Endpoints REST (um arquivo por domínio)
│   ├── core/               # Config, segurança, logging, dependências
│   ├── db/                 # Engine, sessão, Base declarativa
│   ├── models/             # Modelos SQLAlchemy (um arquivo por entidade)
│   ├── schemas/            # DTOs Pydantic (um arquivo por entidade)
│   ├── services/           # Lógica de negócio (um arquivo por domínio)
│   ├── agents/stubs/       # Stubs dos agentes LangGraph (Analyst, Drafter, SDR)
│   ├── websockets/         # Manager WebSocket + Redis Pub/Sub
│   ├── integrations/       # Slack, OpenClaw e outros serviços externos
│   └── main.py             # Ponto de entrada da aplicação
├── alembic/                # Migrations do banco de dados
├── tests/                  # Testes automatizados (pytest-asyncio)
├── scripts/                # Scripts utilitários (seed, etc.)
├── docker-compose.yml      # Ambiente de desenvolvimento local
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Pré-requisitos

- **Docker** e **Docker Compose** (recomendado)
- **Python 3.12+** (para execução local sem Docker)
- **Node.js 24.18.0 LTS** (frontend; use a versão de `frontend/.nvmrc`)

---

## Sidecars OSS locais

Chatwoot e Rocket.Chat rodam em perfis independentes, somente em loopback:

```bash
./scripts/start_oss_sidecars.sh all
.venv/bin/python scripts/verify_oss_sidecars.py --env-file .local/oss.env --profile all --runtime
```

Consulte [docs/OSS_LOCAL_RUNTIME.md](docs/OSS_LOCAL_RUNTIME.md) para pins, isolamento, healthchecks e encerramento.

## Stack PostgreSQL/Redis local em Docker

### 1. Clonar e configurar variáveis de ambiente

```bash
git clone https://github.com/kleislm/klm-legal-hub-backend.git
cd klm-legal-hub-backend
cp .env.example .env
# Configure apenas segredos locais aleatórios do banco/Redis nesta fase.
```

### 2. Subir os serviços

```bash
docker compose up -d
```

Isso sobe:
- **PostgreSQL** na porta `5432`
- **Redis** na porta `6379`
- **API FastAPI** na porta `8010`

### 3. Executar as migrations

```bash
docker compose exec api alembic upgrade head
```

### 4. Inicializar o ambiente demonstrativo local

```bash
docker compose exec api python scripts/seed.py
```

O comando usa o mesmo bootstrap idempotente do SPA. Ele exige o runtime local,
mantém integrações externas bloqueadas e identifica os registros como
demonstrativos; não cria senha fixa nem dados apresentados como clientes reais.

### 5. Acessar a documentação da API

- **Swagger UI:** http://localhost:8010/docs
- **ReDoc:** http://localhost:8010/redoc
- **Health Check:** http://localhost:8010/health

O acesso de revisão é criado pelo bootstrap local somente em loopback. Não há credencial de teste fixa documentada.

---

## Rodando Localmente (sem Docker)

```bash
# Criar e ativar ambiente virtual
python3.12 -m venv .venv
source .venv/bin/activate

# Instalar dependências
pip install -r requirements.txt

# Configurar variáveis de ambiente
cp .env.example .env
# Edite o .env com DATABASE_URL e REDIS_URL apontando para instâncias locais

# Executar migrations
alembic upgrade head

# Inicializar o ambiente demonstrativo local
python scripts/seed.py

# Iniciar servidor
uvicorn app.main:app --reload --port 8000
```

---

## Endpoints Principais

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `POST` | `/api/v1/auth/login` | Autenticação — access token curto e refresh em cookie HttpOnly |
| `GET` | `/api/v1/matters` | Listar casos (Kanban/Lista) |
| `POST` | `/api/v1/matters` | Criar novo caso |
| `GET` | `/api/v1/matters/{id}` | Detalhe completo do caso |
| `PATCH` | `/api/v1/matters/{id}` | Atualizar campos do caso |
| `PUT` | `/api/v1/matters/{id}/move` | Mover card no Kanban |
| `POST` | `/api/v1/matters/{id}/notes` | Adicionar observação na timeline |
| `POST` | `/api/v1/matters/{id}/tasks` | Criar tarefa |
| `PATCH` | `/api/v1/tasks/{id}/toggle` | Alternar status da tarefa |
| `POST` | `/api/v1/matters/{id}/documents` | Iniciar upload (retorna URL S3) |
| `POST` | `/api/v1/documents/{id}/confirm` | Confirmar upload concluído |
| `GET` | `/api/v1/approvals/pending` | Listar aprovações pendentes |
| `POST` | `/api/v1/approvals/{id}/resolve` | Aprovar / Rejeitar |
| `GET` | `/api/v1/reports/metrics` | KPIs e métricas do dashboard |
| `POST` | `/api/v1/webhooks/openclaw` | Webhook de processos judiciais |
| `WS` | `/api/v1/ws` | WebSocket para eventos em tempo real |

---

## Testes

```bash
# Com Docker
docker compose exec api pytest tests/ -v

# Local
pytest tests/ -v --cov=app --cov-report=term-missing
```

---

## Variáveis de Ambiente

Consulte o arquivo `.env.example` para a lista completa de variáveis necessárias.

As variáveis críticas para uma fase futura são:
- `SECRET_KEY` — chave secreta para JWT (gerar com `openssl rand -hex 32`)
- `DATABASE_URL` — URL do PostgreSQL
- `REDIS_URL` — URL do Redis
- `KLM_MODEL_APIS_ENABLED` e `KLM_ONLINE_APPROVED` — gates cumulativos do gateway de modelos
- chave do provider aprovado — armazenada em secret manager, nunca no frontend ou Git
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — credenciais AWS para o S3

---

## Roadmap

- [x] **Fase 1:** Estrutura base, modelos, schemas, services e endpoints REST
- [x] **Fase 2:** WebSockets com Redis Pub/Sub para tempo real
- [x] **Fase 3:** Approval Queue (Human-in-the-loop)
- [x] **Fase 4:** Stubs dos agentes LangGraph (Analyst, Drafter, SDR)
- [x] **Fase 5:** Webhooks (OpenClaw) e integrações (Slack)
- [x] **Fase 6:** Runtime de agentes API-first, RAG, aprovações e flows persistidos no escopo local
- [ ] **Fase 7:** Canário com APIs de modelos, somente após aprovação do pacote local
- [ ] **Fase 8:** Conectores externos e AWS, cada um com autorização separada

---

## Estrutura Monorepo

Este repositório é o monorepo do KLM Hub:

```
klm-legal-hub-backend/
├── frontend/   ← Frontend React + Vite
├── app/        ← Backend FastAPI
├── alembic/    ← Migrations
└── scripts/    ← Seed e utilitários
```

As alterações da SPA, do backend e dos adapters locais são mantidas e testadas juntas neste repositório.
