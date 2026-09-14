# Plano profundo — Módulo Financeiro do Meu Executivo (2026-07-24)

**Documento único e consolidado.** Substitui as Partes B e C de
`PLANO_PENDENCIAS_E_FINANCEIRO_2026-07-24.md` (que permanece válido para a
Parte A — pendências gerais). Somente planejamento; execução sob aprovação.

## 0. Premissas fixas do titular

1. UI travada: **a lógica da UI já está como o titular deseja — NÃO
   redesenhar.** Pode ACRESCENTAR caixas/botões e mover; **nunca remover,
   nunca mudar tipografia/tamanhos/fontes**. Todo bloco novo SEGUE a lógica e
   os padrões vigentes (mesmos componentes — KlmPanel/Primitives/shadcn do
   projeto —, mesma escala, mesmos espaçamentos, mesmo comportamento de
   estados vazio/erro): um bloco novo deve parecer que sempre esteve lá.
   Menu lateral esquerdo intocável.
2. Tudo local antes de subir; gates externos fechados; sandbox/mocks até AWS.
3. Fail-closed sempre; backend é a autoridade; IA nunca inventa valor.
4. Superfícies: **`/app`** = escritórios · **`/blackbox`** = adm da plataforma
   · páginas públicas fora das duas (`/pagar/{token}`).
5. Números de honorário são do advogado: o sistema registra, extrai e executa
   — nunca altera.

---

## 1. Estado atual (verificado no código)

**Camada SaaS→escritório:** modelos `Plan`/`Subscription`/`WorkspaceSubscription`
prontos; **webhook Stripe real** (assinatura verificada, recibos idempotentes
`StripeWebhookEventReceipt`, eventos checkout/invoice/subscription); painéis
MRR/ARR; `Checkout.tsx` é formulário de lead ("Sob consulta") — **não cria
sessão de pagamento**.

**Camada escritório→cliente:** `FinanceEntry` (livro-razão manual: tipo,
status, valor, vencimento, `provider`, `external_ref` já existem) +
`FinanceReminder` + visão geral; credencial Asaas cifrada por workspace
(`IntegrationConnection`, enum `asaas`/`conta_azul`); metadados da integração
(base v3, modelo de segurança de webhook descrito); **gate
`financial_upgrade`** com providers pausados (`FinanceUpgradePauseNotice`) — o
ponto de acoplamento natural deste plano. **Não existem:** cliente Asaas,
meios de pagamento, recorrência, parcelamento, link/checkout, conciliação,
despesas estruturadas, honorários tipados, régua pós-vencimento.

**Veredito:** um caderno com lembretes. Registra e avisa; não cobra.

---

## 2. Benchmark e posicionamento

Líderes 2026: **Projuris ADV** (financeiro mais profundo: DRE, fluxo),
**EasyJur** (conta PJ integrada), **Astrea** (simples; sem timesheet, régua
fraca — nosso teardown), **ADVBOX** (CRM). Open Finance maduro (Fase 5,
1.000+ instituições; agregadores Pluggy/Belvo/Celcoin).

**Diferenciais que nenhum tem** (o "mais avançado que o mercado"):
1. Cobrança conversacional via WhatsApp oficial com agente governado +
   aprovação humana (infra provada).
2. Atualização monetária **jurídica** automática no atraso (Selic/IPCA/Lei
   14.905) — cobrança calculada como advogado calcula.
3. Êxito disparado por **evento processual** (integração com casos).
4. Conciliação com IA **fail-closed** (classificação prévia + conferência
   humana obrigatória).
5. **F11**: nós de cobrança montados automaticamente **lendo o contrato** do
   cliente, com aprovação do financeiro.

---

## 3. Conhecimento Asaas consolidado (fonte: docs.asaas.com, lida em 2026-07-24)

### 3.1 Fundamentos
- Instituição de pagamento regulada pelo BACEN; PCI-DSS; status page própria.
- **Base URLs:** produção `https://api.asaas.com/v3` · sandbox
  `https://api-sandbox.asaas.com/v3` (conta sandbox separada em
  sandbox.asaas.com; aprovação automática; **confirmação manual de pagamento**
  para simular PIX/boleto pagos; cartões de teste fictícios). Nunca misturar
  chave/URL de ambientes.
- Autenticação: API key no header **`access_token`** (exatamente esse nome —
  NÃO é `Authorization: Bearer`), + `Content-Type: application/json` +
  `User-Agent` identificando a aplicação (obrigatório). Prefixos de chave:
  produção `$aact_prod_`, sandbox `$aact_hmlg_`. [verificado na doc]

### 3.2 Cobranças (`POST /v3/payments`)
- Obrigatórios: `customer`, `billingType`, `value`, `dueDate`.
- **`billingType`: `BOLETO` | `CREDIT_CARD` | `PIX` | `UNDEFINED`** — o
  `UNDEFINED` deixa o cliente escolher o meio no checkout ⇒ resolve o
  "vários meios" com UMA cobrança. Restrição: um billingType por cobrança.
- Parcelamento nativo: `installmentCount` + `installmentValue` **ou**
  `totalValue` (divide igualmente).
- Objetos `discount` (com prazo), `fine` (multa pós-vencimento) e `interest`
  (juros a.m.) aplicados automaticamente pelo Asaas; limites legais BR:
  multa ≤ 2%, juros ≤ 1% a.m. (relação de consumo) — confirmar shape exato
  no spike (página específica indisponível na leitura).
- `externalReference` = **nosso ID** (chave de conciliação); `split[]`;
  `callback` (redirect pós-pagamento → volta para o Meu Executivo).
- Resposta do create: `id`, `status` (demais campos como `invoiceUrl`/
  `bankSlipUrl` a confirmar no sandbox — a leitura da doc não os listou).
- **QR Code PIX NÃO vem na resposta do create**: endpoint separado
  `GET /v3/payments/{id}/pixQrCode` → imagem base64 + payload copia-e-cola +
  data de expiração. QR dinâmico, uso único, válido até 12 meses após o
  vencimento; funciona para billingType `PIX`, `BOLETO` e `UNDEFINED`.
  [verificado na doc]

### 3.3 Ciclos de status (mapeados dos webhooks)
- Boleto: `CREATED → CONFIRMED → RECEIVED`.
- PIX: `CREATED → RECEIVED` (instantâneo).
- Cartão: `CREATED → CONFIRMED → RECEIVED` (**D+32** após confirmação — fluxo
  de caixa projetado deve modelar esse prazo).
- Atraso: `→ OVERDUE`; estorno: `REFUND_IN_PROGRESS → REFUNDED` (e parcial);
  chargeback: `CHARGEBACK_REQUESTED → DISPUTE → AWAITING_REVERSAL`.

### 3.4 Assinaturas (`POST /v3/subscriptions`)
- É um **agendador de cobranças**: gera uma cobrança por ciclo, cada uma com
  vida própria (recebe os mesmos webhooks de payment).
- `cycle`: `WEEKLY|BIWEEKLY|MONTHLY|BIMONTHLY|QUARTERLY|SEMIANNUALLY|YEARLY`;
  `nextDueDate` (1ª cobrança), `endDate`/`maxPayments` (término), `fine`/
  `interest`/`discount`/`split` herdados.
- Cartão: validação na criação, cobrança no vencimento. **Trial = nextDueDate
  futuro.**

### 3.5 Links de pagamento (`POST /v3/paymentLinks`)
- Checkout hospedado standalone: `chargeType` **`DETACHED`** (avulsa),
  **`RECURRENT`** (assinatura) ou **`INSTALLMENT`** (parcelado);
  `billingType UNDEFINED` (cliente escolhe o meio); `value` opcional (pagador
  digita — útil para doar/complementar); `maxInstallmentCount`,
  `dueDateLimitDays`, `endDate`. Resposta = URL compartilhável.
- Estratégia: nossa página `/pagar/{token}` apresenta o resumo com a nossa
  cara e delega o pagamento ao link/fatura Asaas (PCI fica todo lá).

### 3.6 Webhooks (a espinha da conciliação em tempo real)
- Até **10 URLs**, seleção granular de eventos; verificação pelo header
  **`asaas-access-token`** (token que NÓS definimos; comparar em tempo
  constante).
- Entrega **at-least-once** com **ID de evento repetível ⇒ dedupe
  obrigatória por event id** (recibo idempotente, molde do Stripe receipt).
- **Fila sequencial que PAUSA após 15 falhas consecutivas (não-2xx)** e só
  reativa manualmente (dashboard/API); retenção de eventos: **14 dias**.
  ⚠️ Consequências de projeto: (a) nosso endpoint responde 200 rápido e
  processa **assíncrono** (enfileira e devolve); (b) monitor de "fila
  pausada" com alerta no `/blackbox` + reativação; (c) reconciliação
  periódica via API (3.8) cobre janelas perdidas.
- **~29 eventos de payment mapeados na leitura** (a doc avisa que novos podem
  ser adicionados — o parser deve tolerar eventos desconhecidos), principais
  para nós:
  `PAYMENT_CREATED/UPDATED/CONFIRMED/RECEIVED/OVERDUE/DELETED/RESTORED/
  REFUNDED/PARTIALLY_REFUNDED/BANK_SLIP_VIEWED/CHECKOUT_VIEWED/
  CHARGEBACK_*`, risco de cartão (`AWAITING/APPROVED/REPROVED_BY_RISK_ANALYSIS`,
  `CREDIT_CARD_CAPTURE_REFUSED`), negativação (`DUNNING_*`), split
  (`SPLIT_CANCELLED/DIVERGENCE_BLOCK*`). Payload: `{id, event, dateCreated,
  account, payment{...}}` + opcionais `subscription`/`installment`/
  `paymentLink`. Tolerar campos novos (não quebrar parser).

### 3.7 Split
- `walletId` de contas Asaas destino; `fixedValue` (2 casas) e/ou
  `percentualValue` (4 casas), calculados sobre o **netValue** (líquido de
  taxas); nunca indicar a própria wallet; status
  `PENDING/AWAITING_CREDIT/DONE/REFUSED/REFUNDED/CANCELLED`. Base pronta
  para repasse a sócio/parceiro (fase 2).

### 3.8 Extrato Asaas (`GET /v3/financialTransactions`) — fonte da conciliação Via B
- Filtros `startDate/finishDate`, paginação `offset/limit` (máx 100),
  `totalCount/hasMore`.
- **60+ tipos** de transação (`PAYMENT_RECEIVED`, `PAYMENT_FEE` — a taxa vira
  DESPESA automática!, `PAYMENT_REVERSAL`, `PIX_TRANSACTION_CREDIT`,
  `TRANSFER`, antecipação...). Valor com sinal (crédito +, débito −).
- Uso duplo: (a) conciliação da conta Asaas; (b) **rede de segurança dos
  webhooks** (poll periódico compara extrato × nossos charges).

---

## 4. Arquitetura alvo

### 4.1 Camada de abstração
`PaymentGatewayPort` (interface): create_customer, create_charge,
create_subscription, create_payment_link, cancel, refund, get_statement.
**`AsaasAdapter`** é a 1ª implementação (molde dos adapters de IA: httpx,
fail-closed, timeout/retry, atrás de `external_delivery_allowed()`; sandbox
em dev; **FakeGatewayAdapter determinístico** para testes/local). Mercado
Pago/Pagar.me entram depois pela mesma porta.

### 4.2 Modelo de dados (tabelas novas — todas workspace_id + RLS + migração + literal no teste RLS)

| Tabela | Campos-chave | Observações |
|---|---|---|
| `finance_charges` | entry_id FK, gateway(asaas), external_id (id Asaas), billing_type, charge_kind(avulsa/parcela/recorrencia), status interno, value, due_date, paid_at, net_value, fee_value, invoice_url, bank_slip_url, pix_payload, installment_ref, subscription_ref, external_reference(=nosso id), payload_snapshot JSON | 1 FinanceEntry ↔ 0..1 charge; série de parcelas = N entries filhas cada uma com charge |
| `finance_recurrences` | client_id, matter_id?, kind(assinatura_gateway/geracao_programada), cycle, value, next_run, end_date/max_count, billing_type, status, gateway_subscription_id? | duas estratégias: assinatura nativa Asaas OU gerador próprio (worker) p/ casos que o Asaas não cobre |
| `asaas_webhook_receipts` | event_id UNIQUE, event_type, received_at, processed_at, status, payload | molde StripeWebhookEventReceipt; dedupe at-least-once |
| `bank_transactions` | source(asaas/ofx/openfinance), external_fingerprint UNIQUE(fitid/hash), date, value(signed), description, raw | ingestão idempotente — nunca duplicar |
| `reconciliation_matches` | bank_transaction_id, target_kind(charge/entry/expense), target_id, method(deterministico/ia/manual), confidence, suggested_tags[], status(sugerido/confirmado/rejeitado), confirmed_by, ai_suggestion_snapshot | trilha IA-sugeriu × humano-confirmou |
| `finance_expense_*` | despesas: categoria, centro_custo, caso?, reembolsavel, comprovante_ref, recorrência própria, aprovação | pode ser extensão do FinanceEntry(type=payable) + tabelas satélites |
| `fee_agreements` (F11) | client_id, matter_id, contract_document_ref, extracted_terms JSON (com confiança + citação/página por campo), proposed_nodes JSON, status(rascunho/aguardando_financeiro/aprovado/rejeitado), approved_by, contract_version | reabre em aditivo |

### 4.3 Máquina de estados do charge (nossa, mapeada dos eventos Asaas)

`rascunho → criada(PAYMENT_CREATED) → [visualizada(BANK_SLIP/CHECKOUT_VIEWED)]
→ confirmada(CONFIRMED) → recebida(RECEIVED) | vencida(OVERDUE) →
[renegociada → nova série] | estornada(REFUNDED) | chargeback(CHARGEBACK_*) |
cancelada(DELETED)`. Toda transição: evento na timeline do caso + auditoria +
gatilhos (kanban financeiro, régua, KPIs).

### 4.4 Segurança/consistência transacional
- Webhook: verificar `asaas-access-token` → gravar recibo → **200 imediato**
  → processar via Celery (idempotente por event_id). Monitor de fila pausada
  (15 falhas) com alerta `/blackbox` + botão reativar.
- Poll de reconciliação (extrato 3.8) diário como rede de segurança.
- Nenhum dado de cartão no nosso banco (PCI no Asaas); apenas refs/urls.
- Dupla porta para dinheiro: `external_delivery_allowed()` E confirmação
  humana no primeiro envio de cada régua/cobrança nova.

---

## 4.5 PRINCÍPIO DA SIMPLICIDADE — "1 clique" (premissa do titular)

O advogado não é financeiro. Toda jornada tem que caber em UM clique com
defaults inteligentes; configuração avançada existe, mas escondida atrás de
"opções". Critérios vinculantes:

**Cobrar em 1 clique:**
- Botão **"Cobrar"** na entrada financeira / no caso / no card do Kanban →
  modal já preenchido (valor e cliente puxados do caso, vencimento default,
  meio = "cliente escolhe" [`UNDEFINED`]) → confirmar → o sistema **cria a
  cobrança, gera boleto + PIX + link automaticamente e ENVIA sozinho**:
  e-mail com o link/fatura (notificação nativa do Asaas —
  `notificationEnabled`, default true — + nosso e-mail com a marca do
  escritório) e WhatsApp com o link (gated, template fora da janela).
- Toast de sucesso com **"copiar link"** e **"reenviar"**. Segunda via = 1
  clique. Nada de formulário longo: o caminho feliz são 2 cliques no total.
- Recorrência/parcelamento: mesmos 2 cliques, só muda um seletor
  ("uma vez / parcelado em N / mensal").

**Conciliar em 1 clique:**
- Tela estilo **caixa de entrada**: cada transação chega com a sugestão
  pronta (categoria + tags + vínculo, com a confiança visível). Ações:
  ✓ confirmar (1 clique), ✎ corrigir, ✗ rejeitar. Botão **"confirmar todos
  os óbvios"** (alta confiança) em lote. Atalhos de teclado (j/k/enter).
- O que o sistema cobrou concilia SOZINHO (match por `externalReference`) —
  nem aparece para conferência, só no log.
- Importar extrato = arrastar o arquivo OFX/CSV na tela (drag-and-drop).

**Painéis e relatórios excelentes e prontos:**
- Dashboard financeiro do escritório **pronto sem configurar nada**: caixa do
  mês, a receber (com D+32 do cartão modelado), vencidos com botão de régua,
  próximas recorrências, aging visual.
- Relatórios de 1 clique: DRE do mês, fluxo de caixa, inadimplência —
  exportam em **Word timbrado** (padrão da casa), PDF e CSV, prontos para o
  contador, sem montar nada.
- Todo número clicável → abre a lista do que o compõe (drill-down).

**Regra de ouro:** se uma jornada exigir mais de 2 cliques no caminho feliz
ou qualquer conhecimento de "gateway/billingType/webhook", ela falhou o
aceite de UX — refazer. Os termos técnicos NUNCA aparecem para o advogado
("meio de pagamento: o cliente escolhe", nunca "billingType UNDEFINED").

## 5. Fases de execução (consolidadas, com critérios de aceite)

### F1 — Fundação transacional
AsaasAdapter + FakeGateway; sync Client↔customer; webhook receiver completo
(token, recibo, 200-rápido, worker); tabelas `finance_charges` +
`asaas_webhook_receipts`; RLS + migrações.
**Aceite:** sandbox cria cobrança PIX e boleto; webhook simulado dá baixa
automática; dedupe provada por teste (evento duplicado = no-op); fila
pausada detectada em teste.

### F2 — Cobrança avulsa multi-meios + checkout do cliente
Criar cobrança de uma FinanceEntry (meio fixo ou `UNDEFINED`); página pública
`/pagar/{token}` (molde `/agendar/{token}`: resumo com nossa marca → botão ao
`invoiceUrl`); conciliação automática entry↔charge por `externalReference`;
multa/juros/desconto pactuados enviados no create.
**Aceite:** fluxo criar→link→pagar(sandbox)→webhook→entry paga→timeline do
caso, ponta a ponta em teste E2E.

### F3 — Recorrência e parcelamento
Assinatura nativa (`/subscriptions`, trial=nextDueDate) para cartão/ciclos
padrão; **gerador próprio** (worker Celery `finance_recurrence_tick`,
idempotente por workspace+recorrência+competência) para o que o gateway não
cobre; parcelamento nativo (`installmentCount/totalValue`) com N entries
filhas.
**Aceite:** relógio congelado gera exatamente 1 cobrança por competência;
cancelamento/pausa; parcela quitada não re-gera.

### F4 — Régua de cobrança (dunning) conversacional
Pré-vencimento (lembretes existentes) + pós: D+1 gentil, D+7 segunda via,
D+15 formal — WhatsApp gated (MetaPolicyGate/template) pelo agente `cobranca`
(hierarquia existente, sob `financeiro`), aprovação humana nos degraus
sensíveis; hermes avisa o dono em críticos; eventos `OVERDUE`/`VIEWED`
alimentam a régua; atualização monetária jurídica no valor em atraso.
**Aceite:** régua dispara nos dias certos (relógio congelado), nunca sem
gate; recalculo Selic/IPCA correto contra casos de referência.

### F5 — Checkout real do SaaS (camada 1)
Stripe Checkout Session + Billing Portal; price IDs; `Checkout.tsx` ganha
botão real (aditivo). Webhook existente já processa o pós.
**Aceite:** assinar plano em modo teste ativa `WorkspaceSubscription`.

### F6 — UI financeiro `/app` (só acréscimos)
Caixas: “Nova cobrança” (meio/recorrência/parcelas), status do gateway na
lista, copiar link, aging de inadimplência; painel do dono (recebimentos do
mês, inadimplência, próximas recorrências). Tipografia intocada.

### F7 — Conciliação bancária automática (IA + conferência humana)
**Fontes:** Via A extrato bancário — começar por **OFX/CSV import** (universal,
custo zero) e plugar agregador Open Finance (Pluggy/Belvo/Celcoin) na v2;
Via B extrato Asaas (`financialTransactions` — taxa `PAYMENT_FEE` vira
despesa automática).
**Pipeline:** ingestão idempotente (`bank_transactions`) → matching
determinístico (externalReference/valor+data) auto-concilia cobranças nossas
→ IA (classe SIMPLE) sugere categoria+**tags**+vínculo com confiança →
**fila de conferência humana obrigatória** (aceitar/corrigir/rejeitar, lote)
→ correções viram regras determinísticas.
**Aceite:** transação duplicada nunca duplica; nada concilia sem confirmação;
recibo Asaas casa sozinho com o charge.

### F8 — Despesas completas
Avulsa + recorrente (mesmo worker F3); vínculo a caso com **reembolsável** →
gera cobrança de reembolso (F2); centro de custo/rateio; comprovante;
aprovação acima de limite; baixa pela conciliação (F7).

### F9 — Honorários jurídicos + cobranças avançadas
Modelos nativos: fixo/entrada parcelada, retainer mensal, **timesheet por
hora** (lançamentos → fatura mensal), **êxito por evento processual** (base
de cálculo registrada), quota litis, híbridos; tabela por área/produto.
Avançado: renegociação de vencidas (novo plano de parcelas auditado),
segunda via/troca de meio no `/pagar/{token}`, retry de cartão + aviso,
gestão de assinaturas (painel churn/próximo ciclo/falhas), split de repasse
(fase 2, `walletId` sobre netValue).

### F10 — KPIs, DRE e fluxo projetado
Backend real para a família "Financeiro" da KpisPage: DRE gerencial (receita
por tipo × despesa por categoria), **fluxo realizado + projetado**
(recorrências + parcelas + êxito ponderado + **prazo D+32 do cartão**),
aging/DSO, recuperação da régua, rentabilidade por caso/advogado,
inadimplência por canal (tracking), export CSV/JSON + relatório Word timbrado.

### F11 — Nós de cobrança lidos do CONTRATO (aprovação do financeiro)
IA lê o contrato (OCR/extração existentes) → extração estruturada com
**confiança + citação literal + página por campo** → monta nós (F2/F3/F9) em
**rascunho** → **fila de aprovação do papel financeiro** (agente `financeiro`
prepara, nunca aprova) com tela termo×trecho×nó → aprovado ativa; aditivo
reabre. Fail-closed: ambíguo = campo vazio; IA nunca altera números; manual
sempre disponível.

### F12 — Webhook de leads de sites (Central de Conexões) [ordem do titular]

**"Formulário do site → direto pro CRM."** Qualquer site (landing page,
WordPress/Elementor, Framer, institucional) envia o formulário para um
webhook do workspace e o lead nasce no CRM automaticamente.

- **Backend:** endpoint público `POST /api/v1/webhooks/leads/{token}` —
  token opaco por workspace (gerável/rotacionável na Central de Conexões,
  cifrado no molde BYOA). Aceita JSON ou form-encoded com **mapeamento
  tolerante** (name/nome, email, phone/telefone/whatsapp, message/mensagem +
  campos extras preservados em metadata; UTMs capturados). Cria contato +
  card no Kanban (`novo_lead`) + timeline de origem ("veio do site X");
  dedupe por (workspace, email|telefone) em janela curta — reaproveita o
  lead existente em vez de duplicar. Anti-abuso: rate-limit por token,
  honeypot opcional (campo `_hp` preenchido = descarta silencioso), tamanho
  máximo. Fail-closed: payload ilegível → 200 com registro de rejeição (não
  vaza erro para script kiddie) + item de revisão.
- **Guia na Central de Conexões (UI, Onda 4):** card "Formulário do site"
  com passo a passo copiável — URL do webhook pronta, exemplos prontos de
  integração (HTML `fetch` de 5 linhas, WordPress/Elementor webhook nativo,
  Zapier/Make genérico), botão "enviar teste" que dispara um lead de exemplo
  e mostra ✓ quando chega. **Regra de 1 clique: copiar URL + colar no site =
  funcionando.**
- **Lead no Kanban → Conversas → disparo automático (ordem do titular):**
  ao criar o card, o sistema também cria/abre a conversa do lead no chat
  (thread omnichannel) e **dispara a primeira mensagem via WhatsApp
  automaticamente** — com uma verdade da Meta respeitada: lead de site nunca
  nos escreveu, logo **não há janela de 24h → o primeiro contato EXIGE
  template aprovado** (business-initiated; o MetaPolicyGate já modela isso).
  Gates fechados → mensagem vira rascunho pendente visível na conversa.
  **Falha nunca é silenciosa:** sem telefone/telefone inválido/sem conexão
  WhatsApp/sem template aprovado/erro de envio → o card recebe flag visível
  "primeiro contato falhou: <motivo>", nasce tarefa para humano agir e o
  painel de conexões registra o erro. Relatório de disparos do funil mostra
  enviados × falhas por motivo.
- SDR/flows: lead do site entra no mesmo funil dos demais (gatilho novo_lead
  existente decide o resto — nada novo a inventar).

### F13 — Indicações com comissão e repasse [ordem do titular]

**Lead por indicação; se a indicação tem comissão, registra e repassa via
financeiro.**

- **Origem "indicação"** no lead/caso: quem indicou (contato/cliente
  existente, parceiro cadastrado ou advogado) — campo estruturado, não texto
  livre.
- **Cadastro de parceiros/indicadores** com acordo de comissão: percentual
  sobre honorário recebido OU valor fixo por caso fechado; vigência; obs.
  Regra do titular: percentuais são decisão humana — o sistema registra e
  executa, nunca sugere/altera.
- **Acúmulo automático:** quando um pagamento é RECEBIDO (o processador de
  webhook F1 já sabe o momento exato), se o caso tem indicador com acordo →
  gera **despesa a pagar** (F8, categoria `comissao_indicacao`) vinculada ao
  indicador e ao pagamento-origem, em `pending_approval` (humano confirma o
  repasse — dinheiro nunca sai sozinho). Idempotente por pagamento (1
  recebimento = no máx. 1 comissão).
- **Repasse:** v1 = despesa aprovada + baixa manual/conciliação (F7); v2 =
  split nativo Asaas (`walletId` sobre netValue) quando o indicador tiver
  conta Asaas.
- **Visão:** painel de indicações (quem indicou, casos, comissões acumuladas/
  pagas) no financeiro; LTV por indicador nos KPIs (F10).
- LGPD: indicador é dado pessoal — mesmas regras de acesso do CRM.

### Ordem e dependências
```
F1 → F2 → F3 → F8 → F7 (precisa de charges+despesas p/ casar)
              ↘ F9 → F11 (precisa dos tipos de nó) → F4 (régua com juros jurídicos)
F5/F6 em paralelo após F2 · F10 por último (agrega tudo)
```

---

## 6. Superfícies (mapa fixo)

| Recurso | Superfície |
|---|---|
| Todo o financeiro do escritório (F1-F4, F6-F11) | **`/app`** |
| Página de pagamento | **pública** `/pagar/{token}` (ticket curto, escopado, expira) |
| Billing SaaS, MRR/ARR, volume por workspace, saúde de gateways/webhooks (inclui monitor de fila Asaas pausada), Operação de IA | **`/blackbox`** |

RBAC separado por superfície; nada do escritório vaza ao `/blackbox` além de
agregados; nada de plataforma no `/app`.

---

## 7. Riscos priorizados

1. **Cobrar duas vezes** = pior erro. Mitigação: idempotência em 3 camadas
   (event_id do webhook, fingerprint do extrato, competência da recorrência) +
   testes dedicados de duplicata.
2. **Fila de webhook pausada** (15 falhas) = baixa para de chegar em silêncio.
   Mitigação: 200-rápido/assíncrono + monitor com alerta + poll do extrato.
3. **D+32 do cartão** distorce caixa se ignorado — modelar no projetado.
4. **LGPD/PCI**: nunca dado de cartão local; links/refs; página pública com
   ticket expirante.
5. **Falso-positivo da IA na conciliação** — por isso conferência humana
   obrigatória e regra-por-correção (nunca auto-aprender silencioso).
6. Shape exato de `fine/interest/discount` e limites: **confirmar no spike
   F1 em sandbox** (página da doc indisponível na leitura de hoje).

## 8. Decisões pendentes do titular

1. Agregador Open Finance já na F7 ou começar OFX/CSV+Asaas (recomendo a 2ª)?
2. Timesheet dentro do MVP F9 ou fase seguinte?
3. Split de repasse confirmado como fase 2?
4. Negativação (Serasa via Asaas `DUNNING_*`) — incluir na régua F4 como
   degrau final opcional (com dupla aprovação humana)?

*Fontes: docs.asaas.com (visão geral, payments, subscriptions, paymentLinks,
webhooks de cobrança, sobre webhooks, split, financialTransactions, sandbox —
lidas em 2026-07-24); benchmark e Open Finance conforme
PLANO_PENDENCIAS_E_FINANCEIRO_2026-07-24.md (Parte C).*
