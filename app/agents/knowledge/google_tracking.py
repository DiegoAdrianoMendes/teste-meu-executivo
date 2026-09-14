"""Base de conhecimento verificada — Google Ads / tracking / GTM / GA4.

Condensado de `docs/GOOGLE_TRACKING_KNOWLEDGE_2026-07.md` (verificado em
25/07/2026). Fonte de verdade única para os agentes nativos de tráfego e
tracking do Meu Executivo — os agentes NUNCA devem inventar fato de API que
não esteja aqui; itens novos exigem atualizar primeiro o dossiê em `docs/`.

Este módulo não faz nenhuma chamada de rede: é apenas texto embutido no
`SYSTEM_PROMPT` dos agentes em `app/agents/stubs/klm_trafego_google.py` e
`app/agents/stubs/klm_tracking_engineer.py`.
"""

GOOGLE_TRACKING_KNOWLEDGE_VERSION = "google-tracking-knowledge-2026-07"

GOOGLE_TRACKING_KNOWLEDGE: str = """\
DOSSIÊ GOOGLE TRACKING — verificado em 25/07/2026 (fonte primária, não inferir além disto).

1. GOOGLE ADS API — ESTADO ATUAL
- Versão atual: v25 (anunciada 22/07/2026). Ciclo de release mensal desde 2026 \
(4 majors/ano, minors não quebram).
- BREAKING CRÍTICO: desde 15/06/2026, upload de conversões offline e Enhanced \
Conversions for Leads foram migrados para a Data Manager API e BLOQUEADOS na \
Google Ads API. Todo código novo de conversão server-side usa a Data Manager \
API — NUNCA o ClickConversionService (depreciado para este fluxo).
- Desde abril/2026 o Google Ads aceita dados user-provided simultaneamente de \
tags do site, Data Manager e API (não é mais escolha exclusiva).
- Auth: OAuth2 + developer token (níveis: test → basic → standard).

2. LEAD FORM WEBHOOK (Google Ads → CRM)
Payload `WebhookLead` (POST JSON):
- `lead_id`: string única. Entrega NÃO é exactly-once → dedupe obrigatório por \
lead_id (mesmo padrão UNIQUE+SAVEPOINT dos demais webhooks).
- `google_key`: chave configurada no Google Ads; validar em constant-time antes \
de processar (equivalente ao token do Asaas).
- `is_test`: true = lead de teste → aceitar com 200, marcar como teste, não \
criar caso real.
- `user_column_data[]`: `column_id` tipado (FULL_NAME, EMAIL, PHONE_NUMBER…), \
`string_value`; `column_name` está depreciado.
- Outros campos: `api_version`, `form_id`, `campaign_id`, `adgroup_id`, \
`creative_id`, `asset_group_id`, `gcl_id` (click id — guardar para conversão \
offline), `lead_submit_time`, `lead_source`, `lead_stage`.
- Respostas: 200 sucesso; 4xx não retenta; 5xx retenta. Parser ignora campos \
desconhecidos (forward-compat).
- `gcl_id` deve ser persistido para fechar o loop lead → CRM → conversão.

3. GA4 MEASUREMENT PROTOCOL (server-side)
- Endpoint `POST /mp/collect` com `measurement_id` (do data stream) + \
`api_secret` (por stream — segredo de servidor, NUNCA no cliente).
- `client_id` obrigatório (ou `user_id`); `timestamp_micros` aceita eventos \
retroativos de até 3 dias (fuso da propriedade).
- Nomes de eventos/parâmetros reservados da coleta automática não podem ser \
enviados por MP; relatórios podem ficar parciais sem gtag/GTM.

4. GOOGLE TAG MANAGER — WEB + SERVER-SIDE
- Arquitetura recomendada: container web (gtag → GTM) + container \
server-side (sGTM) recebendo o stream e decidindo o que repassa a GA4/Ads. \
Endpoint first-party melhora qualidade de dado e controle de PII.
- Consent Mode v2 é obrigatório para mercados com consentimento (análogo \
LGPD): sinais `ad_storage`, `analytics_storage`, `ad_user_data`, \
`ad_personalization`; o web container repassa o estado de consentimento ao \
sGTM em cada hit; tags de Ads exigem `ad_storage=granted`. Modo avançado \
envia pings cookieless para modelagem de conversão.
- Setup padrão a recomendar ao escritório-cliente: (1) criar contêiner GTM \
web + snippet no site; (2) CMP/banner LGPD gravando Consent Mode v2; (3) GA4 \
config tag + eventos de formulário; (4) Google Ads Conversion + Remarketing \
tags condicionadas ao consentimento; (5, avançado) sGTM em subdomínio \
próprio para tracking first-party.

5. GOOGLE PARTNERS
- 3 pilares checados diariamente: Performance, Spend (gasto gerenciado) e \
Certifications (Skillshop ativas na conta da empresa). Badge Partner e \
Premier (top 3%/ano).
- O Meu Executivo opera como parceiro gerenciando contas dos escritórios \
(MCC/manager account), habilitando developer token de nível adequado e \
acesso à Data Manager API em nome dos clientes.

6. PLUGA E ZAPIER (rotas intermediadas)
- Pluga: integração por webhooks (enviar/receber); 100+ ferramentas BR. O \
webhook genérico de leads já serve como destino de qualquer automação Pluga.
- Zapier: rota por webhook (Zap: trigger na ferramenta origem → action \
"Webhooks by Zapier" POST no endpoint). Payload versionado + dedupe já \
cobertos pelo contrato existente.

7. GOVERNANÇA (regra fixa para os agentes de tracking)
- Agentes de tracking são consultivos e read-only por padrão: geram plano de \
contêiner, checklist de tags, diagnóstico de conversão.
- Qualquer mutação em conta Google (criar conversão, subir audiência, editar \
tag publicada) é ação externa → passa pelos gates (`external_delivery_allowed`) \
e por aprovação humana (HARD_HUMAN_GATES), como todo agente do sistema.
- Nunca inventar IDs, chaves ou métricas: dados de conta só via integração \
conectada; sem conta conectada, o agente entrega o plano e a lista do que \
falta — nunca um número fabricado.

FONTES:
- https://developers.google.com/google-ads/api/docs/conversions/upload-offline
- https://developers.google.com/google-ads/webhook/docs/implementation
- https://ads-developers.googleblog.com/2026/07/announcing-v25-of-google-ads-api.html
- https://support.google.com/analytics/answer/9900444
- https://developers.google.com/analytics/devguides/collection/protocol/ga4/sending-events
- https://developers.google.com/tag-platform/tag-manager/server-side/consent-mode
- https://support.google.com/google-ads/answer/9702205
- https://github.com/PlugaDotCo/pluga-docs-developers
"""

GOOGLE_TRACKING_KNOWLEDGE_SOURCES: tuple[str, ...] = (
    "https://developers.google.com/google-ads/api/docs/conversions/upload-offline",
    "https://developers.google.com/google-ads/webhook/docs/implementation",
    "https://ads-developers.googleblog.com/2026/07/announcing-v25-of-google-ads-api.html",
    "https://support.google.com/analytics/answer/9900444",
    "https://developers.google.com/analytics/devguides/collection/protocol/ga4/sending-events",
    "https://developers.google.com/tag-platform/tag-manager/server-side/consent-mode",
    "https://support.google.com/google-ads/answer/9702205",
    "https://github.com/PlugaDotCo/pluga-docs-developers",
)
