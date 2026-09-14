# Plano — Pendências gerais + Módulo Financeiro (2026-07-24)

**Somente planejamento. Nenhuma execução até aprovação.**

## Premissas do titular (fixas)

1. **UI travada na forma atual:** pode ACRESCENTAR caixas/botões e MOVER coisas
   de lugar; **NADA de remover** e **NADA de mudar tipografia/tamanhos**. A
   escala tipográfica commitada em `4ba37db` é o estado final.
2. Menu lateral esquerdo intocável.
3. Tudo local antes de subir (AWS/go-live só quando o titular mandar).
4. Fail-closed sempre; gates externos fechados; backend é a autoridade.

---

# PARTE A — Execução do que ficou pendente

Estado no tip (`850a690`): UI normalizada ✅ · S1-S6 de IA ✅ (S6 completo:
catálogo canônico de fallback + `ai_gateway_events` + endpoints
`/platform/ai-operations/*` + `PlatformAiOperationsPanel`) · spike do Flow
Studio ✅ (padrão schema-driven provado, guards verdes) · auditoria de
referências ✅ · plano mestre ✅.

### P0 — Destravar a árvore (bloqueia commits limpos)
- A sessão do Codex acumulou **195 arquivos** uncommitted (auth, RBAC,
  security, copilot, responsivo, deploy). Ela precisa **fechar e commitar**.
- Depois: commitar os 4 arquivos do spike que ficaram entrelaçados
  (`flow.py`, `flows.py`, `test_flows.py`, `FlowsPage.tsx` — registro v2 do
  condition + `SchemaDrivenNodePanel` + wiring).

### P1 — Onda 0 do plano mestre (higiene, ~meio dia)
- Unificar a **chave duplicada** `whatsapp_connections` no
  `local_schema.py` (2 entradas; a 2ª sobrescreve a 1ª) + teste AST
  anti-duplicata + teste de upgrade legado idempotente.
- Remover `MessageInput 2.tsx` (lixo que quebra o teste de duplicatas).
- Regenerar scorecard vinculado ao fonte atual.
- **Revalidar as 6 falhas conhecidas do backend** (o sistema evoluiu; a lista
  pode ter mudado) e zerar.

### P2 — Onda 1: Proteção real (segurança, shadow-first)
Conforme plano mestre: re-autorização server-side universal, rate-limit por
rota (reusar padrão memory→redis), detecção de abuso em **shadow mode**
(detecta+alerta, não bloqueia até calibrar), revogação por `auth_epoch`,
tickets curtos de documento, painel de incidentes no super admin.
Parte AWS (WAF/CloudFront) diferida ao go-live.

### P3 — Financeiro (PARTE B abaixo) — **recomendo priorizar antes do Flow**
Receita viável > editor bonito. O Flow já funciona; cobrança viável não existe.

### P4 — Onda 2: Flow Studio incremental (liberada pelo spike)
Migrar nós ao padrão schema-driven **por lotes** (5-6 nós por leva), painel
novo convivendo com o antigo atrás de flag, depois: validação `/validate`,
overlay SSE, subfluxos. Sem troca atômica. Sem tocar no executor.

### P5 — AWS/go-live (quando o titular mandar)
Runbook pronto (`RUNBOOK_AWS_FASE1.md` + handoff Codex): push, prova de
release em staging, WAF, Tech Provider, abertura consciente dos gates.

---

# PARTE B — Módulo Financeiro: diagnóstico e plano

> **SUPERSEDIDO:** as Partes B e C foram consolidadas e aprofundadas em
> `docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md` (com o conhecimento da
> documentação Asaas). Este texto permanece só como histórico; a Parte A
> (pendências gerais) continua válida.

## B.1 O que EXISTE hoje (verificado no código)

O financeiro tem **duas camadas** e ambas estão incompletas de formas
diferentes:

### Camada 1 — SaaS cobra o escritório (plataforma)
| Peça | Estado |
|---|---|
| Modelos `Plan`/`Subscription`/`WorkspaceSubscription`+eventos | ✅ prontos |
| **Webhook Stripe real** (assinatura verificada, recibos idempotentes, `checkout.session.completed`/`invoice.paid`/`payment_failed`/`subscription.deleted`) | ✅ pronto |
| Painéis admin (MRR/ARR/billing) | ✅ prontos |
| **Criação de checkout real** | ❌ **não existe** — `Checkout.tsx` é formulário de lead com preço "Sob consulta"; nenhuma sessão Stripe é criada |
| Portal do assinante (trocar cartão, faturas) | ❌ não existe |

### Camada 2 — Escritório cobra o cliente dele (o coração do pedido)
| Peça | Estado |
|---|---|
| Livro-razão manual: `FinanceEntry` (receber/pagar, vencimento, status, valor) + lembretes + visão geral | ✅ pronto |
| Credencial Asaas cifrada por workspace (`IntegrationConnection`, enum já tem `asaas`/`conta_azul`) | ✅ pronta |
| Metadados da integração Asaas (base_url v3, docs, segurança de webhook descrita) | ✅ prontos |
| **Cliente Asaas real** (criar cobrança/assinatura/link) | ❌ **não existe** — só guarda credencial |
| **Recorrência** | ❌ zero (nenhum campo/worker de recorrência) |
| **Parcelamento** | ❌ zero |
| **Link de pagamento / checkout do cliente final** | ❌ zero |
| **PIX / boleto / cartão** | ❌ zero (nenhum meio de pagamento real) |
| **Webhook Asaas → conciliação automática** | ❌ zero (baixa é manual) |
| Régua de cobrança de inadimplente (dunning) | ⚠️ só lembrete pré-vencimento; nada pós-vencimento |
| Gate `financial_upgrade` (providers pausados aguardando upgrade) | ✅ existe — é o ponto de acoplamento natural do plano |

**Veredito:** hoje o financeiro é um **caderno de anotações com lembretes** —
registra e avisa, mas **não cobra**. Para "cobranças e checkouts viáveis com
recorrência e vários meios", falta toda a espinha transacional.

## B.2 Decisões de arquitetura propostas (para aprovação)

1. **Gateway do escritório→cliente: Asaas.** Motivos: já está no enum e na
   tela de integrações; é o gateway BR com PIX + boleto + cartão + assinatura
   recorrente + parcelamento + link de pagamento numa API só; webhook com
   token de validação (o modelo de segurança já está descrito nos metadados).
   Alternativas (Mercado Pago, Pagar.me) ficam como 2º provider futuro atrás
   da mesma interface.
2. **Gateway do SaaS→escritório: manter Stripe** (webhook já pronto) para
   cartão; avaliar Asaas como opção BR (PIX/boleto) numa fase 2.
3. **Dados de cartão NUNCA no nosso banco** (LGPD/PCI): só referências/tokens
   do gateway. O padrão já correto de credencial server-side se mantém.
4. **Tudo fail-closed e atrás dos gates**: nenhuma chamada real ao Asaas/Stripe
   até `KLM_EXTERNAL_ACTIONS_ENABLED`; localmente, client mockado/sandbox e
   testes com recibos idempotentes (espelhar o padrão do webhook Stripe e do
   BYOA).

## B.3 Plano de execução do financeiro (fases F1-F6)

### F1 — Fundação transacional (backend)
- `AsaasClient` (httpx, fail-closed, timeout/retry, atrás dos gates; sandbox
  em dev).
- Sincronização de cliente: `Client` do caso ↔ customer Asaas (id externo).
- **Webhook receiver** `/webhooks/asaas` com validação de token + recibo
  idempotente (tabela própria, molde `StripeWebhookEventReceipt`) + RLS.
- Novas tabelas (todas com `workspace_id` + RLS + migração + literal do teste):
  `finance_charges` (cobrança no gateway: meio, status, link, refs),
  `finance_recurrences` (regra: intervalo, dia, fim, próxima execução).
- Vínculo `FinanceEntry ↔ finance_charge` (1 entrada pode ter 1 cobrança).

### F2 — Cobrança avulsa multi-meios + checkout do cliente final
- Criar cobrança a partir de uma `FinanceEntry`: escolhe meio (**PIX /
  boleto / cartão**) ou "cliente escolhe" (link Asaas com os 3).
- **Página pública `/pagar/{token}`** (molde da `/agendar/{token}` que já
  existe): resumo da cobrança + botão que leva ao link do gateway. Token
  curto, escopado, expira — mesmo padrão de ticket do plano de segurança.
- Conciliação: webhook `PAYMENT_RECEIVED/CONFIRMED` → entry `paga` +
  `paid_at` + evento na timeline do caso + auditoria.

### F3 — Recorrência e parcelamento
- **Recorrência**: assinatura nativa Asaas (cartão/PIX recorrente) OU geração
  programada de cobranças avulsas (boleto/PIX mensal) — worker Celery
  (`finance_recurrence_tick`) idempotente por período (dedupe key
  workspace+recorrência+competência).
- **Parcelamento**: N parcelas vinculadas (série), usando o parcelamento
  nativo do Asaas onde couber; entries filhas por parcela.
- Honorários de êxito/quota litis ficam FORA da recorrência (são evento, não
  assinatura) — entram como cobrança avulsa ligada ao caso.

### F4 — Régua de cobrança (dunning) integrada aos agentes
- Escalonamento pós-vencimento: D+1 lembrete gentil, D+7 segunda via +
  aviso, D+15 notificação formal — **via WhatsApp gated** (MetaPolicyGate +
  template fora da janela) usando a infra já provada; agente `cobranca` (já
  existe na hierarquia, sob `financeiro`) orquestra com aprovação humana nos
  degraus sensíveis; hermes avisa o dono nos casos críticos.
- Kanban financeiro: cartão muda de coluna por status real do gateway.

### F5 — Checkout real do SaaS (camada 1)
- Stripe Checkout Session de verdade nos planos (price IDs), sucesso/cancelamento,
  trial; o webhook existente já processa o resto.
- Portal do assinante (Stripe Billing Portal) para trocar cartão/ver faturas.
- `Checkout.tsx`: **aditivo** — os cards atuais ganham botão "Assinar agora"
  (nada removido; preços reais quando definidos).

### F6 — UI do financeiro (só ACRÉSCIMOS, tipografia intocada)
- Página financeira ganha caixas novas: **"Nova cobrança"** (meio,
  recorrência, parcelas), coluna/badge de status do gateway na lista, botão
  **copiar link de pagamento**, bloco de inadimplência (aging: a vencer /
  vencido 7/15/30+).
- Painel do dono: recebimentos do mês, taxa de inadimplência, próximas
  recorrências.
- Super admin: nada novo obrigatório (MRR já existe); opcional: visão de
  volume transacionado por workspace.

### Transversal (todas as fases)
- RBAC: capability financeira já existente estende para cobranças (criar/
  cancelar exige permissão; visualizar é separado).
- Auditoria KlmAuditEvent em toda ação de cobrança.
- Testes: unit + API + webhook idempotente + RLS + recorrência (relógio
  congelado) + E2E do fluxo criar→link→webhook→baixa.
- Localmente: TUDO mockado/sandbox; nenhuma cobrança real até AWS + gates.

## B.4 Ordem recomendada e dependências

```
P0 (árvore) → P1 (higiene) → F1→F2→F3→F4 (financeiro núcleo) → P2 (segurança shadow)
                                   ↘ F5/F6 em paralelo quando F2 estabilizar
Depois: P4 (Flow Studio incremental) → P5 (AWS/go-live, sob ordem)
```

**Justificativa da prioridade:** cobrança viável é receita e é o maior gap
funcional do produto; Flow Studio já funciona (feio→ok depois da tipografia) e
tem spike aprovado esperando — pode esperar o financeiro sem risco.

## B.5 Riscos específicos do financeiro

- **Dinheiro real = gates duplos**: toda criação de cobrança passa por
  `external_delivery_allowed()` E por confirmação explícita de valor/cliente
  (nunca cobrança automática sem humano no primeiro envio de cada régua).
- **Idempotência absoluta** em webhook e worker de recorrência (cobrar duas
  vezes é o pior erro possível — usar o padrão reserva→liquidação já provado).
- **LGPD/PCI**: nenhum dado de cartão no banco; links/refs apenas.
- **Conta Azul** (contábil) fica fase futura — não misturar com o transacional.

---

# PARTE C — Upgrade pós-pesquisa de mercado (ordem do titular)

## C.1 Benchmark: o que os líderes têm e onde vamos passá-los

Pesquisa (2026): os 4 líderes BR são EasyJur, Astrea (Aurum), Projuris ADV e
ADVBOX. Referências financeiras do mercado:

| Concorrente | Ponto forte financeiro | Nossa resposta |
|---|---|---|
| **Projuris ADV** | O financeiro mais profundo (DRE, fluxo de caixa) | DRE + fluxo **projetado** com recorrências e êxito probabilístico (C.5) |
| **EasyJur** | Conta PJ digital integrada | Não viramos banco: **Open Finance** conecta a conta que o advogado JÁ tem (C.2) |
| **Astrea** | Simplicidade; mas sem timesheet e régua fraca (teardown próprio) | Timesheet por caso + régua via WhatsApp oficial com IA (C.4) |
| **ADVBOX** | CRM + tarefas | Nosso CRM já supera; financeiro integrado ao funil |

**Diferenciais que NENHUM deles tem** (o "mais avançado que o mercado"):
1. **Cobrança conversacional**: régua de inadimplência pelo WhatsApp oficial
   com agente de IA governado + aprovação humana (nossa infra já provada).
2. **Atualização monetária jurídica automática** na cobrança vencida
   (Selic/IPCA/Lei 14.905 — know-how que já dominamos) — juros e correção
   calculados como um advogado calcula, não como um ERP genérico.
3. **Êxito vinculado a evento processual**: honorário de êxito dispara
   cobrança quando o andamento/trânsito acontece (integração com o módulo de
   casos que só nós temos).
4. **Conciliação com IA fail-closed**: classificação prévia por IA + fila de
   conferência humana obrigatória (C.2) — não a "conciliação cega" do mercado.

## C.2 F7 — Conciliação bancária automática (IA classifica, humano confirma)

**Fontes de dados (duas vias, configuráveis por workspace):**
- **Via A — Extrato bancário (Open Finance)**: agregador (Pluggy, Belvo ou
  Celcoin — decidir por cobertura/custo; Open Finance está maduro em 2026,
  Fase 5, 1.000+ instituições) puxa transações da conta que o escritório já
  usa. **Fallback universal: importação OFX/CSV manual** (todo banco exporta).
- **Via B — Asaas**: extrato/movimentações da própria conta Asaas (já teremos
  o client da F1) — cobre quem centraliza recebimento no gateway.

**Pipeline (fail-closed, no nosso DNA):**
1. Ingestão → `bank_transactions` (workspace_id + RLS, idempotente por
   fitid/hash da transação — **nunca duplicar lançamento**).
2. **Matching automático determinístico** primeiro: valor+data+ref → casa com
   `FinanceEntry`/`finance_charge` existente (recebimento de cobrança nossa
   concilia sozinho, alta confiança).
3. **IA classifica o resto** (classe SIMPLE do gateway — barato): sugere
   categoria, **tags**, vínculo provável (caso/cliente/despesa) e confiança.
4. **Fila de conferência manual**: NADA vira conciliado sem humano confirmar
   (aceitar/corrigir/rejeitar em lote; 1 clique no óbvio). Correções
   alimentam regras determinísticas (aprendizado por regra, não retreino).
5. Auditoria completa: quem confirmou, quando, o que a IA sugeriu vs o final.

**Tags e categorias:** taxonomia própria do escritório (editável) + categorias
padrão jurídicas (custas, diligência, perícia, honorário recebido, repasse,
pró-labore, software, aluguel...). Tag livre multi-etiqueta por lançamento.

## C.3 F8 — Despesas (contas a pagar completo)

- Despesa avulsa e **recorrente** (aluguel, software, salários) com o mesmo
  worker de recorrência da F3.
- **Vínculo a caso/cliente** (custas, diligência, perícia) com marcação
  **reembolsável** → gera cobrança de reembolso ao cliente (via F2) e entra
  no custo do caso.
- Centro de custo (por área, por sócio, por unidade) e rateio.
- Anexo de comprovante/NF (storage existente) + status (a pagar → pago) com
  baixa manual ou por conciliação (C.2 casa o débito do extrato).
- Aprovação de despesa acima de limite (workflow humano, RBAC).

## C.4 F9 — Cobranças avançadas + gestão de assinaturas e honorários "como nós"

**Modelos de honorário (todos nativos, vinculados ao caso):**
- **Fixo/entrada** (parcelável — F3), **mensalidade/retainer** (assinatura
  recorrente — F3), **por hora/timesheet** (lançamento de horas por
  advogado/caso → fatura mensal consolidada), **êxito** (% sobre proveito,
  disparado por evento processual, com base de cálculo registrada),
  **quota litis** e híbridos (entrada + êxito). Regra do titular preservada:
  números de tese/percentuais são do advogado — o sistema registra e executa,
  nunca altera.
- **Tabela de honorários por área/produto** (mínimos, padrão OAB como piso
  referencial) para propostas consistentes.

**Cobranças avançadas:**
- Juros + multa + **atualização monetária automática** no atraso (Selic/IPCA
  configurável) — recalculada no dia do pagamento.
- **Renegociação**: parcelar dívida vencida em novo plano (novo contrato de
  parcelas, trilha de auditoria do acordo).
- Split de recebimento (repasse a sócio/parceiro/indicador) — fase 2, via
  split nativo do gateway.
- Segunda via/reemissão de boleto e troca de meio de pagamento pelo próprio
  cliente na página `/pagar/{token}`.
- **Gestão de assinaturas** (visão do escritório): painel de recorrências
  ativas/pausadas/canceladas, churn de mensalistas, próximo ciclo, falhas de
  cartão com retry automático do gateway + aviso na régua.

## C.5 F10 — Dados e KPIs financeiros (superar o Projuris)

A família "Financeiro" já existe no catálogo de KPIs da KpisPage (caixa,
contas a receber, vencido, inadimplência, cobrança, promessa, LTV por canal).
Completar com backend real (fonte: entries + charges + conciliação):

- **DRE gerencial** (receita por tipo de honorário × despesas por categoria,
  mensal/acumulado) e **fluxo de caixa realizado + projetado** (recorrências
  futuras + parcelas + êxito ponderado por probabilidade da fase processual).
- Aging de inadimplência (7/15/30/60/90+), taxa de recuperação da régua,
  tempo médio de recebimento (DSO), inadimplência por canal de aquisição
  (liga no tracking que já existe).
- Rentabilidade **por caso** (honorários − despesas do caso − custo de
  timesheet) e por área/advogado.
- LTV por cliente e por canal (já parcialmente na KpisPage).
- Exportação CSV/JSON (padrão já existente na KpisPage) + relatório mensal
  em Word timbrado (nosso padrão) para o contador.

## C.6 Ordem revisada do financeiro

```
F1 fundação → F2 cobrança avulsa+checkout → F3 recorrência/parcelas → F8 despesas
   → C.2 conciliação (precisa de charges+despesas existirem p/ casar)
   → F9 honorários avançados/renegociação → F4 régua → F10 KPIs/DRE → F5/F6
```

Racional: conciliação só faz sentido depois que existem cobranças e despesas
para casar; honorários avançados montam sobre recorrência/parcelas; KPIs por
último porque agregam tudo.

## C.7 F11 — Configuração de cobrança dirigida pelo CONTRATO (ordem do titular)

**"O sistema coloca os nós por cliente lendo os contratos, automaticamente,
pedindo aprovação do financeiro."**

Fluxo:
1. **Leitura do contrato** do cliente (PDF/Word/imagem — pipeline de
   OCR/extração que já existe) quando anexado ao caso ou no onboarding.
2. **Extração estruturada por IA** (structured output, schema fechado) dos
   termos financeiros: tipo de honorário (fixo/entrada/mensalidade/hora/
   êxito/quota litis/híbrido), valores, percentuais, nº de parcelas, dia de
   vencimento, índice de correção, multa/juros pactuados, condições do êxito.
   Cada campo com **confiança** e **trecho-fonte** do contrato (citação
   literal + página) para conferência.
3. **Proposta de configuração**: o sistema monta os "nós" de cobrança do
   cliente (entradas F2, parcelas/recorrências F3, êxito F9) em estado
   **rascunho — nada ativo**.
4. **Aprovação do financeiro (gate humano obrigatório)**: item na fila de
   aprovação (padrão ApprovalQueue existente) para o papel financeiro do
   workspace (RBAC — capability própria; o agente `financeiro` da hierarquia
   pode preparar, nunca aprovar). Tela mostra lado a lado: termo extraído ×
   trecho do contrato × nó proposto. Aprovar ativa; editar corrige; rejeitar
   descarta.
5. **Auditoria completa**: contrato-fonte, versão, o que a IA extraiu, o que
   o humano aprovou/alterou.

**Regras fail-closed (invioláveis):**
- Confiança baixa ou termo ambíguo → campo fica vazio pedindo preenchimento
  humano, **nunca valor inventado** (mesmo princípio do S1a).
- **A IA nunca altera valores/percentuais** — extrai e cita; números são do
  advogado (regra do titular já vigente).
- Nenhuma cobrança nasce ativa sem a aprovação do financeiro; alteração de
  contrato (aditivo) reabre o ciclo com nova aprovação.
- Contrato sem termos financeiros legíveis → notifica e cai para configuração
  manual (o fluxo manual da F2/F3/F9 continua existindo sempre).

Dependências: F1-F3 + F9 (os tipos de nó precisam existir antes do montador
automático). Encaixa após F9 na ordem do C.6.

## C.8 Superfícies: `/app` × `/blackbox` (mapa do financeiro)

Distinção arquitetural vigente (roteamento por audiência já implementado):
**`/app`** = usuários dos escritórios (workspace) · **`/blackbox`** = painel
administrativo da plataforma (super admin e usuários adm do sistema).

| Recurso do plano | Superfície |
|---|---|
| Financeiro do escritório inteiro: cobranças, checkouts `/pagar/{token}`*, recorrências, despesas, conciliação (fila de conferência), honorários, régua, KPIs/DRE do workspace, aprovação de config por contrato (F11) | **`/app`** |
| Página pública de pagamento `/pagar/{token}` | Pública (fora das duas) |
| Billing do SaaS (planos, assinaturas dos workspaces, Stripe), MRR/ARR, volume transacionado por workspace, saúde de gateways/webhooks, Operação de IA, incidentes de segurança | **`/blackbox`** |

Regra: nenhum recurso financeiro do escritório vaza para `/blackbox` além de
agregados anonimizáveis de operação; nenhum controle de plataforma aparece em
`/app`. RBAC de plataforma (`/blackbox`) e RBAC de workspace (`/app`) seguem
matrizes separadas (matriz de acesso do blackbox já tem teste dedicado).

## C.9 Decisões novas para o titular aprovar

1. **Agregador Open Finance**: Pluggy vs Belvo vs Celcoin (avaliar cobertura
   das contas que os escritórios-alvo usam + custo por conexão) — ou começar
   só com **OFX/CSV + Asaas** (zero custo externo) e plugar agregador na v2.
2. **Timesheet**: entra no MVP de honorários (F9) ou fase seguinte?
3. **Split de repasse**: fase 2 confirmada?

Fontes da pesquisa: [Comparativo softwares jurídicos 2026 (EasyJur)](https://easyjur.com/blog/melhores-softwares-juridicos-comparativo/) ·
[AdvBox vs Astrea vs Projuris 2026](https://seasy.host/2026/04/02/advbox-vs-astrea-vs-projuris-adv-software-juridico-2026/) ·
[Ranking SquadZ 2026](https://asquadz.ai/blog/softwares-gestao-juridica-comparativo/) ·
[Open Finance Brasil 2026 (TecnoSpeed)](https://blog.tecnospeed.com.br/open-finance-brasil/) ·
[Pluggy — API extrato p/ ERPs](https://www.pluggy.ai/blog/api-extrato-bancario-erp-sistema-gestao) ·
[Pluggy — Open Finance 2026](https://www.pluggy.ai/blog/open-finance-2026-novidades) ·
[Celcoin — Open Finance APIs](https://celcoin.com.br/articles/ferramentas-open-finance-integracao-apis/)
