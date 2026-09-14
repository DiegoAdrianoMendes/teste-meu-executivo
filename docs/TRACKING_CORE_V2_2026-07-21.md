# KLM Hub - Tracking Core V2

**Data:** 21 de julho de 2026  
**Ambiente validado:** exclusivamente local  
**Modelo de atribuicao:** `last_paid_touch`  
**Gate de envio externo:** fechado

## Objetivo

O Tracking Core transforma origem, campanha, conjunto, anuncio, criativo,
conversa, card e receita em uma jornada unica e auditavel. A camada local pode
preparar conversoes para os provedores, mas nao declara envio, aceite ou
entrega sem credencial, chamada real e recibo oficial.

## Contrato canonico

Cada evidencia de aquisicao preserva, quando disponivel:

- provedor, conta, campanha, conjunto, anuncio e criativo;
- `utm_source`, `utm_medium`, `utm_campaign`, `utm_content` e `utm_term`;
- identificadores de clique validados por provedor;
- URL de origem, pagina de entrada, referrer e momento do toque;
- cliente, caso e conversa canonicos;
- evento comercial, valor, moeda e identificador de negocio;
- estado do consentimento e referencia da evidencia de consentimento.

O dashboard agrega apenas eventos canonicos. Os eventos brutos continuam
armazenados para auditoria e nunca sao reescritos para fabricar uma atribuicao.

## Atribuicao

O modelo atual e `last_paid_touch`:

1. usa a evidencia valida gravada no proprio evento comercial;
2. se ela nao existir, procura o toque pago valido mais recente do mesmo
   cliente ou caso, anterior ao evento;
3. nao trata apenas campanha ou criativo como identificacao suficiente para
   feedback ao provedor;
4. separa conversa rastreada, conversa nao rastreada e conversao sem
   atribuicao;
5. mantem links de inspecao para conta, campanha, conjunto, anuncio ou
   criativo quando a plataforma oferece identificadores adequados.

## Venda sem duplicidade

`purchase` e `payment_confirmed` podem representar a mesma venda. O KLM Hub
calcula uma identidade comercial com o identificador explicito da transacao,
pagamento, pedido, cobranca ou fatura, sempre limitado ao cliente ou caso
canonico.

- O hash SHA-256 dessa identidade alimenta a chave de idempotencia.
- `payment_confirmed` tem precedencia sobre `purchase` quando ambos descrevem
  a mesma transacao.
- Receita, conversao e feedback contam uma vez.
- O evento bruto duplicado permanece no historico.
- Se o provedor ja recebeu a conversao, um evento posterior nunca dispara um
  segundo envio.
- O dashboard exibe `business_duplicates_suppressed` para auditoria.

## Feedback aos provedores

O envelope local exige consentimento individual afirmativo, referencia da
evidencia e ao menos uma chave de correspondencia valida. Dados de saude,
documentos, relato juridico, mensagens, notas internas e conteudo do RAG nao
entram no payload de marketing.

Contratos preparados:

- Meta Ads: `event_id` deterministico, identificadores aceitos e evento
  comercial mapeado sem contexto juridico sensivel;
- Google Ads: `gclid`, `gbraid` ou `wbraid`, `order_id`, timezone, consentimento
  e validacao separada da execucao;
- TikTok Ads: `ttclid`, matching permitido e chave de deduplicacao para uso
  conjunto de pixel e Events API;
- LinkedIn Ads: `conversionHappenedAt` em milissegundos e `eventId` para
  deduplicacao;
- Microsoft Ads: `msclkid`, identidade permitida e semantica de conversao
  offline.

Referencias oficiais usadas no contrato:

- Meta: <https://developers.facebook.com/docs/marketing-api/conversions-api/parameters>
- Google: <https://developers.google.com/google-ads/api/docs/conversions/upload-offline>
- TikTok: <https://ads.tiktok.com/help/article/events-api?lang=en>
- LinkedIn: <https://learn.microsoft.com/en-us/linkedin/marketing/integrations/ads-reporting/conversions-api-schema?view=li-lms-2026-02>
- Microsoft Ads: <https://learn.microsoft.com/en-us/advertising/campaign-management-service/applyofflineconversions?view=bingads-13>

Antes da producao, cada contrato devera ser revalidado contra a versao da API,
conta, regiao e programa de acesso efetivamente aprovados.

## Evidencia e estados

O sistema distingue:

- `blocked`: requisitos de configuracao, consentimento ou matching ausentes;
- `prepared`: envelope persistido localmente e pronto para revisao;
- `validated`: somente apos validacao real oferecida pelo provedor;
- `accepted`, `sent` ou equivalente: somente com chamada externa e recibo;
- `failed`: resposta real, erro normalizado e tentativa auditada.

Preparacao, tentativa e recibo sao registros separados. A interface nao pode
converter `prepared` em sucesso visual.

O mesmo principio foi aplicado ao transporte oficial de midia das conversas:
intencao idempotente, tentativa, aceite do provedor e receipt imutavel sao
registros distintos. Falhas de payload nao derrubam uma conexao saudavel;
falhas autenticadas de credencial podem marcar a conexao para intervencao. Um
resultado incerto bloqueia repeticao automatica e permanece consultavel por
`intentId`, evitando custo duplicado e divergencia entre frontend e provedor.

## Evidencia automatizada

```text
tests/services/test_ads_conversion_feedback_service.py
tests/services/test_acquisition_context_service.py
tests/api/test_tracking_summary_real.py
tests/api/test_klm_operations.py
tests/migrations/test_ads_conversion_feedback_migration.py
tests/migrations/test_single_alembic_head.py                 102 passed

frontend/src/test/capability-contextual-navigation.test.tsx
frontend/src/test/conversations-tools.test.tsx                44 passed

frontend typecheck                                             passed
eslint focado                                                  passed
ruff F/E9/I focado                                             passed
git diff --check                                               passed
```

O teste de regressao comercial cria `purchase` e
`payment_confirmed` com o mesmo `transactionId` e comprova uma receita, uma
conversao, um envelope e um duplicado comercial suprimido.

## Gates antes da producao

- credenciais e identificadores reais das contas de anuncio;
- configuracao versionada por workspace e por provedor;
- consentimento e referencia de evidencia provenientes do fluxo real;
- verificacao da permissao Tech Provider e dos escopos aprovados;
- validacao real em sandbox ou conta de teste, quando o provedor oferecer;
- armazenamento de request, response, request ID e recibos assinados;
- reconciliacao periodica entre evento local, provedor e venda financeira;
- monitoramento de falhas, retry com lease e dead-letter queue;
- ensaio de exportacao e portabilidade do historico de atribuicao.

Nenhum desses gates foi presumido como concluido nesta fase local.

## Vinculacao final ao release local

O Tracking Core participou das cinco provas formais vinculadas ao snapshot
canonico registrado em `app/data/oss_native_verification.json`. A matriz
integral confirmou:

- `297/297` testes E2E de producao, com tracking renderizado e acionavel em
  desktop, mobile e tablet;
- valores monetarios em formato brasileiro completo, com centavos e sem
  abreviacoes `K` ou `M`;
- 119 verificacoes HTTP e 511 contratos de acao, sem uma unica execucao
  externa;
- `2.420/2.420` testes de backend PostgreSQL, RLS ativo e zero skips;
- deduplicacao comercial, receipts imutaveis, idempotencia de feedback,
  indices de contexto de aquisicao e isolamento por workspace;
- estado final `verified` servido pela API e gate de arquitetura `12/12`.

O tracking detalhado permanece disponivel no Caso 360 e como contexto limitado
dos agentes, incluindo origem, canal, campanha, conjunto, anuncio, criativo,
UTMs, identificadores de clique e links de inspecao quando presentes. Conversa,
card e eventos de aquisicao compoem a recuperacao contextual, sempre sob o
workspace do cliente.

A prova local valida captura, atribuicao, auditoria, preparacao de envelopes e
prevencao de duplicidade. Envio real a Meta, Google, TikTok, LinkedIn ou
Microsoft, reconciliacao com recibos oficiais e operacao na AWS permanecem
fechados ate a fase de credenciais e aprovacao expressa.
