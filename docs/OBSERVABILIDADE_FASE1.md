# Observabilidade mínima — Fase 1

> O que monitorar assim que a Fase 1 subir em AWS (`docs/RUNBOOK_AWS_FASE1.md`),
> com ferramentas free-tier, plugando no que já existe **sem código novo**.
> Nada aqui foi ativado por esta sessão — é o plano para o proprietário
> ligar no dia do go-live.

## 1. O que já existe nativamente no KLM Hub (sem código novo)

| Capacidade | Onde | Como consultar hoje |
|---|---|---|
| **Error tracking interno** | `app/middleware/error_tracking.py` (`ErrorTrackingMiddleware`) — intercepta toda resposta 5xx e exceção não tratada, persiste em `error_logs` com `request_id`, `endpoint`, `user_id`, `workspace_id` | `GET /api/v1/error-logs` e `GET /api/v1/error-logs/summary` (`app/api/v1/endpoints/error_logs.py`) |
| **Custo de IA por workspace** | `AiCostService` (`app/services/ai_cost_service.py`) | `GET /api/v1/platform-admin/ai-costs` (agregado) e `GET /api/v1/platform-admin/ai-costs/{workspace_id}` |
| **Meta-health (funil por workspace)** | `app/api/v1/endpoints/platform_admin.py` | `GET /api/v1/platform-admin/meta-health` |
| **Custo AWS estimado** | Cost Explorer via `AiCostService.get_aws_cost_estimate` | `GET /api/v1/platform-admin/aws-costs` |
| **Health check da aplicação** | `GET /health` (`app/main.py`) | HTTP simples, sem autenticação — é o alvo do uptime check externo (seção 2) |

Todos esses endpoints já existem e já são protegidos por permissão de Super
Admin/plataforma onde faz sentido — não precisam de nenhuma mudança de
código para a Fase 1. O que falta é **plugar sinal externo** (uptime,
alertas) e **rotina de olhar** (quem checa o quê, com que frequência).

## 2. Uptime check externo do `/health`

Ferramenta recomendada (free-tier, sem cartão): **UptimeRobot** (50
monitores grátis, checagem a cada 5 min no free tier) ou **Better Uptime**
free tier (10 monitores, 3 min). Ambas notificam por e-mail/Slack/webhook
sem custo na escala da Fase 1 (1 domínio).

Configuração:
1. Monitor tipo HTTP(S), URL `https://api.<dominio>/health`, esperado
   `200` e corpo contendo o payload de status da aplicação.
2. Intervalo de checagem: 5 min (free tier).
3. Alerta: e-mail do proprietário + (opcional) webhook para o mesmo canal
   usado para alertas de erro (seção 4), para não fragmentar sinal.
4. **Não** expor `/health` com detalhes sensíveis — conferir que a resposta
   atual não vaza segredo (é um status simples, já é o caso hoje).

Nenhuma mudança de código: o endpoint já existe e é público por design.

## 3. Erro novo via error tracking interno

O `ErrorTrackingMiddleware` já persiste todo 5xx/exceção em `error_logs`.
Falta só a **rotina de olhar**:

- **Frequência:** checar `GET /api/v1/error-logs/summary` diariamente na
  primeira semana pós go-live; depois, 2-3x/semana.
- **Alerta ativo (sem código novo):** como não há push automático hoje,
  a forma mais simples de virar isso em alerta ativo sem escrever código é
  um **cron externo simples** rodando `curl` contra
  `GET /api/v1/error-logs/summary` com um token de Super Admin e comparando
  a contagem de novos erros contra a última checagem — pode ser um
  scheduled task do próprio ambiente do proprietário (ex.: GitHub Actions
  `schedule:` gratuito, ou `cron` na própria EC2) que dispara um webhook
  (Slack/e-mail) se `newErrors > 0` desde a última execução. Isso é
  "plugagem", não código novo no produto — o script de polling fica fora do
  `app/**` (ex.: em `scripts/` num sprint futuro, fora do escopo deste).
- **Alternativa mínima sem nenhum script:** revisão manual diária do
  endpoint pelo proprietário/Super Admin durante a primeira semana crítica
  pós go-live (aceitável no volume da Fase 1, 1-20 escritórios).

## 4. Custo de IA por workspace + alerta de budget

- **Consulta:** `GET /api/v1/platform-admin/ai-costs` (agregado) e por
  workspace. Já existe, já funciona, não precisa de nada novo.
- **Rotina recomendada:** checagem semanal manual na Fase 1 (volume baixo
  não justifica automação ainda). Definir um teto mensal de custo de IA por
  workspace (a decidir junto com `docs/PRICING_BILLING_POLITICA_2026-07.md`)
  e comparar manualmente contra `ai-costs` toda semana.
- **Alerta de budget automatizado (AWS, free):** configurar um **AWS Budget**
  (Billing Console → Budgets) com alerta por e-mail quando o custo AWS mensal
  ultrapassar um teto (ex.: R$ 600, acima da estimativa de R$ 450-500/mês da
  Fase 1). AWS Budgets é gratuito até um certo número de budgets/alertas
  (suficiente para 1-2 alertas de conta única) — cobre o risco de custo de
  infraestrutura fugir do controle (ex.: NAT Gateway acidental, instância
  maior que a planejada), complementando (não substituindo) o
  `GET /api/v1/platform-admin/aws-costs` interno.

## 5. Resumo — ferramentas free-tier recomendadas

| Sinal | Ferramenta | Custo | Ação para ativar |
|---|---|---|---|
| Uptime do `/health` | UptimeRobot (ou Better Uptime) | Grátis | Criar conta, cadastrar 1 monitor HTTP |
| Erro novo (5xx) | `error_logs` interno + revisão manual/cron externo | Grátis (já existe) | Nenhuma — só rotina de checagem |
| Custo de IA por workspace | `ai-costs` interno | Grátis (já existe) | Nenhuma — só rotina de checagem |
| Custo AWS / budget | AWS Budgets | Grátis (free tier de budgets) | Criar 1 budget com alerta por e-mail no Billing Console |

Nenhum item desta tabela exige escrever código de produto novo — são
ferramentas externas plugadas nos endpoints/health check que já existem, ou
rotinas manuais de baixo custo adequadas ao volume da Fase 1.

## Pendência

- Nada bloqueante para o go-live: os 4 sinais têm caminho de ativação sem
  código. A automação do alerta de erro (item 3, cron externo) fica como
  melhoria de um sprint futuro se o volume de erros justificar sair da
  checagem manual.
