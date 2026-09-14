# Dossiê Google Tracking — base de conhecimento dos agentes (verificado em 25/07/2026)

Fonte de verdade para os agentes nativos de tráfego/tracking do Meu Executivo.
Todo item [verificado] foi conferido em fonte primária nesta data; itens marcados
"confirmar" exigem checagem na doc oficial antes de virar código de produção.

## 1. Google Ads API — estado atual

- **Versão atual: v25** (anunciada 22/07/2026). [verificado — ads-developers.googleblog.com/2026/07]
- Ciclo de release passou a **mensal** em 2026; 4 majors/ano, minors não-quebram. [verificado]
- **BREAKING CRÍTICO:** desde **15/06/2026**, upload de conversões offline e
  Enhanced Conversions for Leads foram **migrados para a Data Manager API e
  bloqueados na Google Ads API**. Qualquer código de conversão server-side novo
  deve usar a **Data Manager API**, não ClickConversionService. [verificado —
  developers.google.com/google-ads/api/docs/conversions/upload-offline]
- Desde abril/2026 o Google Ads aceita dados user-provided simultaneamente de
  tags do site, Data Manager e API (não é mais escolha exclusiva). [verificado]
- Auth: OAuth2 + developer token (níveis de acesso: test → basic → standard).

## 2. Lead Form Webhook (Google Ads → nosso CRM)

Payload `WebhookLead` (POST JSON): [verificado — developers.google.com/google-ads/webhook/docs/implementation]

- `lead_id` — string única; **entrega NÃO é exactly-once → dedupe obrigatório por lead_id** (mesmo padrão UNIQUE+SAVEPOINT dos nossos webhooks).
- `google_key` — chave configurada no Google Ads; **validar em constant-time antes de processar** (equivalente ao token do Asaas).
- `is_test` — true = lead de teste (aceitar com 200, marcar como teste, não criar caso real).
- `user_column_data[]` — `column_id` tipado (FULL_NAME, EMAIL, PHONE_NUMBER…), `string_value`; `column_name` está depreciado.
- `api_version`, `form_id`, `campaign_id`, `adgroup_id`, `creative_id`, `asset_group_id`, `gcl_id` (click id — guardar para conversão offline), `lead_submit_time`, `lead_source`, `lead_stage`.
- Respostas: **200** sucesso; **4xx** não-retenta; **5xx** retenta. Parser deve ignorar campos desconhecidos (forward-compat).
- Encaixe no Meu Executivo: mesma esteira do F12 (lead → CRM → chat → template WhatsApp ou erro visível), com `gcl_id` persistido para fechar o loop de conversão.

## 3. GA4 Measurement Protocol (server-side)

- Endpoint `POST /mp/collect` com `measurement_id` (do data stream) + `api_secret`
  (por stream, **segredo de servidor — nunca no cliente**). [verificado]
- `client_id` obrigatório (ou `user_id`); `timestamp_micros` aceita eventos
  retroativos de até **3 dias** (fuso da propriedade). [verificado]
- Nomes de eventos/parâmetros reservados da coleta automática não podem ser
  enviados por MP; relatórios podem ser parciais sem gtag/GTM. [verificado]
- Uso no Meu Executivo: eventos de funil server-side (lead_recebido,
  proposta_enviada, contrato_assinado, cobranca_paga) sem depender de JS do site.

## 4. Google Tag Manager — web + server-side

- Arquitetura recomendada: container web (gtag → GTM) + **container server-side**
  (sGTM) recebendo o stream e decidindo o que repassa a GA4/Ads. First-party
  endpoint melhora qualidade de dado e controle de PII. [verificado — visão geral]
- **Consent Mode v2** obrigatório para mercados com consentimento (análogo LGPD):
  sinais `ad_storage`, `analytics_storage`, `ad_user_data`, `ad_personalization`;
  o web container repassa o estado de consentimento ao sGTM em cada hit; tags de
  Ads exigem `ad_storage=granted`. Modo avançado envia pings cookieless para
  modelagem de conversão. [verificado]
- Setup padrão que os agentes devem recomendar ao escritório-cliente:
  1. Criar contêiner GTM web + snippet no site;
  2. CMP/banner LGPD gravando Consent Mode v2;
  3. GA4 config tag + eventos de formulário;
  4. Google Ads Conversion + Remarketing tags condicionadas ao consentimento;
  5. (avançado) sGTM em subdomínio próprio para tracking first-party.

## 5. Google Partners

- 3 pilares checados **diariamente**: Performance, Spend (gasto gerenciado) e
  Certifications (Skillshop ativas na conta da empresa). Badge Partner e Premier
  (top 3%/ano). [verificado — support.google.com/google-ads/answer/9702205]
- Implicação de produto: o Meu Executivo opera como parceiro gerenciando contas
  dos escritórios (MCC/manager account), o que habilita developer token de nível
  adequado e acesso à Data Manager API em nome dos clientes.

## 6. Pluga e Zapier (rotas intermediadas)

- **Pluga**: integração por webhooks (enviar/receber); docs para desenvolvedores
  em github.com/PlugaDotCo/pluga-docs-developers (beta); 100+ ferramentas BR.
  Encaixe: o webhook genérico de leads do F12 já serve como destino de qualquer
  automação Pluga. [verificado]
- **Zapier**: rota por webhook (Zap: trigger na ferramenta origem → action
  "Webhooks by Zapier" POST no nosso endpoint). Payload versionado + dedupe já
  cobertos pelo contrato do F12.

## 7. Regras para os agentes (governança)

- Agentes de tracking são **consultivos e read-only** por padrão: geram plano de
  contêiner, checklist de tags, diagnóstico de conversão. Qualquer mutação em
  conta Google (criar conversão, subir audiência, editar tag publicada) é ação
  externa → passa pelos gates (`external_delivery_allowed`) e por aprovação
  humana (HARD_HUMAN_GATES), como todo agente do sistema.
- Nunca inventar IDs, chaves ou métricas: dados de conta só via integração
  conectada; sem conta conectada, o agente entrega o plano e a lista do que falta.

## Fontes

- https://developers.google.com/google-ads/api/docs/conversions/upload-offline
- https://developers.google.com/google-ads/webhook/docs/implementation
- https://ads-developers.googleblog.com/2026/07/announcing-v25-of-google-ads-api.html
- https://support.google.com/analytics/answer/9900444
- https://developers.google.com/analytics/devguides/collection/protocol/ga4/sending-events
- https://developers.google.com/tag-platform/tag-manager/server-side/consent-mode
- https://support.google.com/google-ads/answer/9702205
- https://github.com/PlugaDotCo/pluga-docs-developers
