# PLANO META MÁXIMO — KLM Hub

**Data:** 23/07/2026 · **Status:** plano aprovado para registro; execução por fases, cada uma com gate de aprovação do proprietário. Subordinado ao V5 (`KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md`): APIs reais, credenciais e AWS permanecem bloqueados até aprovação expressa por fase.

**Objetivo declarado pelo proprietário:** usar TUDO que a Meta oferece, da melhor forma possível, rodando limpo e sem erros, com o máximo de dados e precisão de tracking, devolvendo os dados para a Meta para otimizar as campanhas continuamente.

**Insumos:** 7 auditorias de canal/tracking (23/07, consolidadas em `AUDITORIA_META_CANAIS_2026-07-23.md`) + 3 confrontos backend × frontend (23/07) + levantamento do catálogo Meta 2026 em fontes oficiais.

---

## 0. Estado da plataforma Meta (07/2026) que este plano explora

**WhatsApp Cloud API (único transporte permitido; On-Premises morta em 10/2025):**
cobrança POR MENSAGEM de template (service grátis; utility grátis dentro da janela); tiers de envio 250/1k → 10k → 100k → ilimitado com escalada automática por qualidade + utilização ≥50% em 7 dias; pacing de marketing pela Meta; throughput 80→1.000 mps; novos termos 02/04/2026 (Contact Book); cobrança em R$ pela entidade Meta Brasil.
Recursos: templates (texto, mídia, **carousel**, **cupom**, **oferta por tempo limitado**, **voz**), mensagens interativas (botões, listas, CTA), **WhatsApp Flows** (formulários in-chat, agora com upload de arquivo, assinatura eletrônica, mapa e **pagamento in-chat**), catálogo/produtos (até 30 por mensagem), **Calling API** (voz), typing indicators, read receipts, reações, reply-to (contexto), localização, Embedded Signup para onboarding de clientes, Business Management API (templates, quality, limites), webhooks de status com `pricing` por mensagem.

**Conversions API for Business Messaging (o coração do loop de otimização):**
cobre os 3 canais com identificadores próprios: WhatsApp = `ctwa_clid` (chega no `referral` da 1ª mensagem de anúncio CTWA), Messenger = PSID, Instagram = IGSID. Requer `action_source="business_messaging"` + `messaging_channel` + dataset criado via Dataset API por canal. **14 tipos de evento**: Purchase, LeadSubmitted, InitiateCheckout, AddToCart, ViewContent, OrderCreated, OrderShipped, OrderDelivered, OrderCanceled, OrderReturned, CartAbandoned, **QualifiedLead**, RatingProvided, ReviewProvided. Deduplicação é 100% responsabilidade do anunciante. Otimização de Purchase disponível para Messenger e WhatsApp. CTWA em 2026: até 92% menor custo por lead vs landing page; sem CAPI-BM, Advantage+ aprende mal e o CPL sobe.

**Messenger (pós-deprecações):** janela 24h; HUMAN_AGENT (7d, humano); Message Tags de reengajamento MORTAS (27/04/2026); recurring notifications MORTAS no BR (10/02/2026); reengajamento fora de janela = Sponsored Messages / Click-to-Messenger (mídia paga). Ice breakers, persistent menu, quick replies, m.me com ref, webhooks de read/delivery restaurados.

**Instagram:** só gatilho-first (usuário inicia); janela 24h + HUMAN_AGENT 7d; cap ~200 msgs automatizadas/h POR CONTA; private reply: 1 por comentário, 7 dias (live: só durante transmissão), e NÃO abre janela (ela abre quando o usuário responde no Direct); **Handover Protocol morto no IG → Conversation Routing**; persistent menu agora suportado; reel como attachment no webhook; caminho de login recomendado: Instagram Login (`instagram_business_basic` + `instagram_business_manage_messages` + `instagram_business_manage_comments`), tokens 60d com refresh.

**Ads (aquisição paga):** CTWA (Click-to-WhatsApp), CTM (Click-to-Messenger), CTD (Click-to-Instagram-Direct) via Marketing API; `referral`/`ads_context_data`/refs nos webhooks; insights de spend por campanha/adset/ad; Advantage+ alimentado pelos eventos CAPI.

---

## 1. Arquitetura-alvo (o que "rodar limpo e sem erros" significa em código)

1. **Meta Policy Gate central e canal-agnóstico** (novo módulo `app/services/meta_policy_gate.py`): TODO envio outbound passa por ele antes do dispatch. Verifica por canal: janela (24h WA/IG/Messenger, com clamp fixo), exigência de template fora de janela (WA), gatilho-first e cap 200/h por conta (IG), 1 private reply por comentário + 7 dias (IG), HUMAN_AGENT só `author_type=human` ≤7d, tier/limite diário do número (WA), opt-out/supressão. Falha SEMPRE fechada com razão explícita e legível.
2. **Mapa de erros Meta como política**: cada `provider_status_code` mapeado para ação (skip terminal / opt-out permanente / backoff global / pausa de campanha / parada de conta). Retry NUNCA é cego.
3. **Espinha de atribuição única**: todo sinal de origem (ctwa_clid, referral, ref, ads_context, UTM, gclid, fbclid) capturado no ingest, persistido no lead/caso, e TODO marco do funil emite `AcquisitionEvent`. O CAPI dispatcher devolve os marcos à Meta com dedup por `event_id`.
4. **Nada dormente**: toda capacidade de backend tem superfície de frontend operável; toda promessa de frontend tem lastro no backend (matriz na seção 9).
5. **Gates preservados**: cada fase liga um conjunto explícito de flags com aprovação do proprietário; o fail-closed continua sendo o default de tudo que não foi aprovado.

---

## 2. FASE 1 — WhatsApp produção limpa (2-3 semanas)

O único canal com transporte real. Sai daqui "verde para produção".

### W1. Higiene crítica (bloqueadores, da auditoria)
1. Remover gateway QR não-oficial do caminho de produção (rota `/connections/qr`, strategy, client; `whatsapp.py:345-361`). Cloud API + Coexistence apenas.
2. Mapa de erros Meta (`meta_cloud_client.py:589-613` + `campaign_service.py:2128-2151`): 131050 → opt-out permanente + supressão; 131049 → skip 24h; 131047 → exigir template (terminal); 131026/132000/132001 → terminal; 130429 → backoff do lote inteiro; 368/131031 → PARADA TOTAL da campanha + alerta.
3. Idempotência de envio: claim durável (commit de `processing`) ANTES da chamada; `delivery_uncertain` reconcilia por webhook, nunca retry automático.
4. Kill switch automático: pausa por failure rate do lote (limiar configurável ~15%) e por primeiro erro de conta restrita.
5. Tier e rampa: tabela de tier por conexão + contador rolling 24h de destinatários únicos; recusar/reagendar excedente; rampa de aquecimento para número novo; ETA de campanha considerando pacing da Meta.
6. Template `approved` obrigatório no launch real (`campaign_service.py:707-717`; `pending` só em dry-run).
7. `v20.0` hardcoded → `settings.WHATSAPP_API_VERSION` (`transcription_service.py:45`); subir default (validar Groups API na versão); PRAZO FATAL 24/09/2026.
8. Webhook: responder 200 imediato e processar via Celery (envelope já é persistido antes; infra pronta).
9. Rotação de chave de tokens: MultiFernet versionado + job de re-cifragem; decrypt falho vira conexão em `error` + alerta (nunca `None` silencioso).
10. Re-registro: reutilizar PIN armazenado; tratar 133005.
11. Opt-out inbound automático: SAIR/PARE/STOP/"não quero" → `WhatsAppSuppressionEntry` + auditoria. Webhook de `failed` alimenta supressão (persistir `errors[]` completo do status).
12. Frequency cap por contato: conectar `MessagingCadenceService` (quiet hours, caps) ao motor de campanhas; janela de horário do destinatário.

### W2. Janela de 24h e gestão REAL de templates
1. Rastrear `last_inbound_at` por conversa; bloquear envio livre fora da janela com razão clara + fallback assistido para template utility.
2. **Template Management via Business Management API**: criar/submeter template à Meta pela UI (hoje só sync read-only), acompanhar approved/pending/rejected com webhooks `message_template_status_update`, exibir categoria (marketing/utility/auth) e quality rating do template, alertar recategorização (custo 7x) e pausa de template.
3. Aposentar a ambiguidade da "biblioteca de templates" interna (rotular como "respostas rápidas internas", distinta de templates Meta).

### W3. Custo por mensagem e billing
1. Persistir `pricing` (categoria, billable, pricing_model) de cada status webhook por `wamid` → ledger BRL por workspace/campanha.
2. Custo projetado ANTES do disparo no fluxo do operador (não só `finance:view`): destinatários × categoria × tarifa BR + repasse com markup 20% (política de pricing).
3. Alertas de gasto por campanha e por workspace (integra com budget/entitlements do plano de billing).

### W4. Recursos avançados Meta a adotar (diferencial de produto)
1. **WhatsApp Flows**: (a) qualificação jurídica in-chat (o formulário do SDR vira Flow nativo), (b) agendamento de consulta com slots, (c) upload de documentos do caso, (d) assinatura eletrônica simples, (e) NPS/avaliação. Avaliar pagamento in-chat (entrada de honorário) na fase 2 do billing.
2. **Templates ricos**: carousel (apresentação de serviços/planos do escritório), cupom (campanhas de consulta), oferta por tempo limitado, template de voz.
3. **Calling API**: botão "ligar para o escritório" e chamada iniciada pelo cliente; triagem de voz futura.
4. **UX de conversa**: typing indicator durante processamento do agente, read receipts, reação, reply-to com contexto.
5. Catálogo/produtos: opcional (baixa aderência jurídica); registrar como capacidade não prioritária.

### W5. Frontend WhatsApp (do confronto; nada dormente)
1. **Conexão self-service do número no workspace**: UI de Embedded Signup (reaproveitar `PlatformOfficialWhatsAppPanel.tsx:177`) chamando `whatsapp.py:587`; fallback manual. Hoje `WhatsAppConnections.tsx` é read-only e o tenant não consegue conectar sozinho.
2. **Motivo do erro visível**: `last_error_code/message` no inbox (`ConversationsPage.tsx:830`) e na campanha, com tradução dos códigos comuns.
3. **Indicador de janela de 24h** no composer: aberta/fecha em Xh/fechada → "requer template" com CTA.
4. **Drill-down por destinatário** na campanha (sent/delivered/read/failed/skipped + motivo), usando `CampaignRecipientStatus` já tipado.
5. Custo no passo de confirmação do disparo; badges de categoria e quality no seletor de template.
6. **Painel de saúde do número**: tier atual, utilização do limite 24h, quality rating (verde/amarelo/vermelho) com alerta de queda (requer worker de sync via Business Management API + webhooks; item novo de backend).
7. Origem de anúncio da conversa (ctwa) visível no header do chat e no Caso 360 (depende da Fase 2 T1).

### W6. Central de Conexão guiada (wizard passo a passo com prints REAIS)

Nova superfície `/app/conexoes` (e espelho no admin da plataforma), cobrindo as DUAS jornadas:

**Jornada A — Plataforma (o proprietário, uma vez): tornar-se Tech Provider.**
Tela de admin com checklist vivo e passo a passo ilustrado de cada etapa no ecossistema Meta:
1. Criar app Business no developers.facebook.com (tipo, caso de uso Business Messaging);
2. Business Verification no Business Manager (documentos da empresa, prazos reais);
3. Configurar produto WhatsApp no app + número de teste;
4. Configurar **Embedded Signup** (config_id, allowed domains);
5. App Review: Advanced Access de `whatsapp_business_messaging` + `whatsapp_business_management` (F1) e, nas fases sociais, `pages_messaging` / `instagram_business_*` (screencasts exigidos);
6. Webhooks (URL, verify token, campos assinados) e teste de eco;
7. Datasets via Dataset API (F2) e Marketing API (F5);
8. Linha de status por etapa lida DA API quando possível (status de verificação, permissões concedidas, campos de webhook assinados), não checkbox manual.

**Jornada B — Tenant (cada escritório, self-service): conectar os canais. DOIS MODOS, com detecção automática do disponível:**

**Modo B1 — Manual/BYOA (disponível DIA 1, sem depender do Tech Provider):** cada escritório cria o próprio app no developers.facebook.com e conecta a própria WABA. Operando os próprios ativos, não há App Review; a exigência é a Business Verification do PRÓPRIO escritório. Wizard com prints reais de cada tela:
1. Criar app Business no developers.facebook.com (tipo, nome);
2. Adicionar produto WhatsApp ao app;
3. Criar/vincular WABA e número (verificação SMS/voz, display name conforme diretrizes);
4. Business Verification do escritório no Business Manager (documentos; sem ela fica limitado a números de teste);
5. Criar System User e gerar token permanente com os escopos certos;
6. Configurar webhook: colar a URL por tenant do KLM + verify token exibidos na tela, e o **App Secret do app do escritório** no KLM;
7. Colar credenciais no KLM (token, phone_number_id, WABA id, app secret) → validação automática (eco de webhook + envio de teste) → template inicial submetido → tier do dia exibido.
Troubleshooting embutido: display name recusado, número preso em outra WABA, PIN two-step (133005), verificação pendente, token sem escopo.
Ajuste de backend exigido pelo modo B1: **app secret por conexão** na validação de assinatura do webhook WhatsApp (hoje `WHATSAPP_APP_SECRET` é global; replicar o padrão por-conexão já usado nos canais sociais) e tela de intake de credenciais sobre o endpoint `POST /connections/manual` (existente e hoje órfão).
Vantagem de billing do B1: a cobrança por mensagem da Meta cai direto na conta do escritório (sem repasse de WhatsApp pelo KLM; o KLM cobra plano + IA).

**Modo B2 — Embedded Signup (quando o Tech Provider for aprovado; vira o padrão):** o que preparar antes (conta Meta, número livre de registro anterior, display name); o popup passo a passo (login, escolher/criar Business, criar WABA, informar número, verificação SMS/voz, display name); pós-conexão automática: webhook ok → template inicial submetido → tier atual e limite do dia → primeiro envio de teste. Conexões B1 existentes continuam válidas; migração para B2 é opcional e gradual. O sistema fica PRONTO para o B2 desde a F1 (endpoint `embedded_signup_connect` já existe); o wizard só habilita o modo quando as permissões Advanced Access estiverem concedidas (status lido da API).
2. **Instagram** (F3): OAuth Instagram Login com prints (conta profissional, permissões, seleção de conta), aviso claro dos pré-requisitos (conta Business/Creator) e do cap de automação.
3. **Página/Messenger** (F4): Facebook Login for Business com prints (seleção de Página, permissões), verificação do binding Página→workspace.

**Estratégia de prints (regra: nunca placeholder):** os screenshots são capturados DURANTE a conexão piloto real da própria KLM em cada fase (F1 para WhatsApp, F3 IG, F4 Página) e versionados em `frontend/public/onboarding/`; até a captura, o wizard exibe somente texto passo a passo completo (sem molduras vazias). Cada release do wizard tem os prints revisados contra a UI atual da Meta (ela muda; revisão trimestral entra na rotina de operação).

**Backend de suporte:** endpoint de status de onboarding por conexão (etapa atual, pendências, erros com tradução), reutilizando o preflight já existente do `tech_provider_service` (capacidade/entitlement) e expandindo para os canais sociais.

**Gate de saída F1:** suítes verdes + simulação local de campanha com erros injetados (131050/368/130429) comprovando skip/parada/backoff + veredito do agente `meta-whatsapp-disparos` reprocessado para "SIM, com monitoramento" + wizard da Jornada B (WhatsApp) navegável de ponta a ponta com prints reais capturados na conexão piloto.

---

## 3. FASE 2 — Tracking máximo + loop CAPI permanente (2 semanas; pode iniciar em paralelo à F1)

O coração do pedido: máxima precisão de dados e devolução contínua à Meta.

### T1. Captura total de sinais (ingest)
1. **`ctwa_clid`**: extrair `message.referral` completo no webhook WhatsApp (`webhook_service.py::_handle_inbound_message`): `{ctwa_clid, source_id (ad), source_url, source_type, headline, body, media}` → `thread_metadata.acquisition` + `AcquisitionEvent(conversation_started)` com `click_ids.ctwa_clid`, `dedupe_key=ctwa:{wamid}`. Adicionar `ctwa_clid` a `PROVIDER_CLICK_IDS` e ao adapter.
2. **Backfill histórico**: job read-only sobre `MessageEvidence.event_payload` recuperando referrals de todas as mensagens já recebidas.
3. **Social**: capturar `referral.ref` (m.me/ig.me) e `ads_context_data`; aceitar evento standalone de referral; `correlation.adId` de thread nova → `AcquisitionEvent(conversation_started)` por canal; capturar `ad_id` em comentários de anúncio (hoje só em DM).
4. **Web**: adapter aceita e persiste `utm_source/medium/campaign/term/content` como first-class (hoje só `utm_id`), `fbclid` com reconstrução de `fbc`, `gclid/gbraid/wbraid`.
5. **SDR**: `lead_source` correto no Matter criado por WhatsApp; canal + click_ids herdados do thread.

### T2. Emissão de eventos em TODOS os marcos do funil
Sobre `record_acquisition_event` (idempotente, já existe). Mapa marco → evento interno → evento CAPI:

| Marco | Onde instrumentar | Evento interno | Evento CAPI-BM |
|---|---|---|---|
| 1ª mensagem de anúncio | webhook WA/IG/Messenger | conversation_started | (sinal de referência, não enviado) |
| Lead qualificado pelo SDR | `sdr_intake_service` (qualification completed) | lead_qualified | **QualifiedLead** |
| Lead capturado (form) | já existe | lead_captured | LeadSubmitted |
| Reunião agendada | `meeting_service` (booking confirmado) | meeting_scheduled | InitiateCheckout (proxy de intenção) |
| Proposta enviada | fluxo de proposta/honorário | proposal_sent | AddToCart (proxy) |
| Contrato assinado | webhook ZapSign `doc_signed` (`webhooks.py:442-467`) | contract_created | **OrderCreated** |
| Honorário pago | `finance_service`/Stripe/local billing | payment_confirmed | **Purchase** (com value/currency) |
| Caso perdido | transição Kanban perdido | matter_lost | OrderCanceled (opcional) |
| Avaliação do cliente | NPS/review | rating_provided | RatingProvided |

### T3. CAPI dispatcher (devolver dados à Meta para sempre otimizar)
1. **Datasets**: criação/registro de dataset por canal via Dataset API (por workspace ou da plataforma, decisão de arquitetura: recomendo dataset por workspace para atribuição do cliente + relatório próprio).
2. **Worker de despacho**: consumir `ExternalActionIntent(action_type="ads_conversion_feedback", status=approved)` (hoje preparado e nunca consumido), atrás de `require_external_delivery_allowed`; envelopes por canal:
   - WhatsApp: `action_source=business_messaging`, `messaging_channel=whatsapp`, `user_data.ctwa_clid`;
   - Messenger: PSID; Instagram: IGSID;
   - Web/landing: Pixel + CAPI web com `event_id` compartilhado (dedup navegador × servidor);
   - Conversões tardias: `event_time` retroativo dentro da janela do provedor.
3. **Dedup do anunciante** (obrigatório: a Meta não deduplica BM): `event_id` determinístico por marco (`{workspace}:{event_name}:{matter_id}`), registro de recibo por envio, bloqueio de reenvio.
4. **Qualidade do sinal**: monitorar Event Match Quality; hashing SHA-256 dos identificadores (já pronto); painel de recibos oficiais × falhas (UI já renderiza, falta o produtor).
5. **Google em paralelo** (opcional na fase, recomendado): offline conversion import com `gclid` reaproveitando `_google_envelope`; decide o destino dos serviços mortos (`google_ads_service`/`meta_ads_service`): **reviver como sync read-only de insights + upload de conversões**, não remover.

### T4. Spend e ROI reais
1. Sync read-only de insights/spend da Marketing API (campanha/adset/ad) atrás de gate; fallback: entrada manual + CSV.
2. CAC/ROAS reais por campanha por workspace (motor pronto; passa a ter numerador e denominador).
3. Unificar `reports_service` com o motor de atribuição (eliminar heurística de substring e a dupla fonte de verdade).

### T5. Frontend tracking (do confronto)
1. **Tela de spend**: manual + upload CSV + status do sync oficial (separada do botão de fixture).
2. **Configuração de mensuração**: Pixel ID, dataset ID, token CAPI por workspace em `IntegrationsPage` (hoje só webhook de leadgen); provider Google Ads no catálogo.
3. **Despacho real** na aba Feedback (hoje só "preparar"): botão enviar (gated), recibos oficiais populando, EMQ visível.
4. **ctwa/adId ponta a ponta**: no card de captação, no header da conversa, no Caso 360 e na tabela de atribuição.
5. **Editor de atribuição manual** no Caso 360 (campanha/UTM/adId), expandindo `updateMatter`.
6. **Selo real × simulado** em TODAS as linhas do dashboard (hoje só na aba Captação); fixture nunca se mistura a produção.
7. KPIs: corrigir a descrição aspiracional de `KpisPage` para o que é real, e então torná-la real.

**Gate de saída F2:** em simulação local, um clique CTWA sintético percorre anúncio → conversa → qualificação → contrato → pagamento e aparece: (a) no Rastreamento com jornada completa, (b) na fila CAPI com envelope business_messaging válido e recibo simulado, (c) no ROAS da campanha. Veredito do agente de tracking reprocessado para "SIM".

---

## 4. FASE 3 — Instagram completo e gatilho-first por arquitetura (2 semanas)

### I1. Enforcement de plataforma (da auditoria)
1. Validação `entry.id` × `external_account_id` (rejeitar/quarentenar; campo obrigatório) + receptor único por app roteando por `entry.id` (modelo real da Meta). Fecha o risco cross-tenant.
2. Cap ~200 msgs automatizadas/h POR CONTA no worker (contador por `connection_id`, backpressure via `next_attempt_at`); teto do schema ≤200 para IG; contador separado de mensagens humanas.
3. `rate_limits` e `quiet_hours` das regras APLICADOS em `trigger_active_automations_for_event` (hoje gravados e ignorados).
4. Private reply: dedup cross-regra por `private-reply:{event_id}`; janela de 7 dias verificada no dispatch; live só durante transmissão; retry não-retryable após expiração.
5. Janela 24h: clamp fixo para canais Meta (matar 168h); comentário público NÃO atualiza `last_inbound_at` (janela só com DM real); story mention via `changes` não abre janela (só a mensagem).
6. Campanha IG: bloquear `status=running` sem política; re-shape como "reengajamento em janela" com elegibilidade computada server-side.
7. Loop de auto-resposta: filtro `direction == inbound` no runtime.
8. Aposentar trilho legado Studio (`meta_channels_service.py:822-955`) em favor do v6.

### I2. Onboarding e permissões
OAuth Instagram Login (`instagram_business_basic`, `instagram_business_manage_messages`, `instagram_business_manage_comments`), tokens 60d com worker de refresh (renovar com >24h de vida), Business Verification + Advanced Access com screencasts, Data Deletion Callback, assinatura de webhooks `messages, messaging_postbacks, comments, live_comments, mentions`.

### I3. Recursos Meta a adotar
Ice breakers e persistent menu (agora suportado no IG) configuráveis por conexão; quick replies; reel attachment no webhook (thread com contexto do Reel); read/delivery webhooks; **Conversation Routing** (substituto do handover) para bot ↔ humano; story reply/mention como lead quente com SLA e prioridade no inbox.

### I4. SDR social
Intake social: quando o usuário responde no Direct após private reply, criar/atualizar Matter com origem (comment → post/ad), iniciar qualificação SDR e cadência DENTRO da janela com degradação para WhatsApp/e-mail ao expirar.

### I5. Frontend
UI de conexão OAuth; indicador de janela por conversa (expor `service_window_expires_at` no schema de leitura); rate limits reais e editáveis com selo "aplicado"; capability com `state/source` fiel (verificado × precisa configurar × indisponível); superfície de story mentions; consolidação das pilhas (remover inbox dormente do `MetaChannelStudio` e rotas duplicadas `/instagram/*`).

**Gate de saída F3:** veredito "funil IG 100% gatilho-first" vira SIM por arquitetura; teste E2E local: comentário sintético → 1 private reply exata → resposta do usuário abre janela → SDR qualifica → cap por conta respeitado sob carga.

---

## 5. FASE 4 — Messenger completo (1 semana)

1. Policy Gate cobre Messenger (janela canal-agnóstica; agendamento com `channel=messenger` valida janela ou degrada para WhatsApp/e-mail).
2. HUMAN_AGENT: implementação restrita a `author_type=human` ≤7d + App Review própria; jamais em automação.
3. Campanhas: elegibilidade server-side por `last_inbound_at`; copy sem "broadcast"; superfície re-rotulada "Reengajamento pago" apontando para Sponsored Messages/CTM (Marketing API) com custo de mídia explícito.
4. OAuth de Página (Facebook Login for Business, `pages_messaging` Advanced Access), tokens por conexão, `entry.id` binding (mesma correção da F3).
5. Ice breakers, persistent menu, m.me com `ref` capturado como origem; capability matrix honesta (matar defaults all-true do legado).
6. Frontend: conexão OAuth, janela visível, gate persistente também na pilha que sobrar.

**Gate de saída F4:** parecer do canal reprocessado; zero promessa de outbound orgânico fora de janela em qualquer superfície.

---

## 6. FASE 5 — Aquisição paga integrada (2 semanas)

Fecha o ciclo "campanha → conversa → receita → sinal → campanha melhor".

1. **Reviver `meta_ads_service` com governança**: sync read-only de campanhas/insights/spend (alimenta T4); criação de campanha CTWA/CTM/CTD via Marketing API atrás de aprovação humana por ação (ExternalActionIntent + approval flow já existentes na plataforma).
2. Welcome messages/ice breakers por anúncio (contexto do anúncio no primeiro contato); mapeamento anúncio → playbook do SDR (o playbook é esforço do V5).
3. Painel de Aquisição: funil por campanha (cliques → conversas → qualificados → contratos → receita), CPL/CAC/ROAS, recomendação de realocação; alertas de campanha com CPL fora da meta.
4. Google Ads paralelo (se aprovado): sync + offline conversions com gclid.
5. Frontend: gestão de campanhas de aquisição com dry-run/aprovação, atribuição visível por anúncio, EMQ e recibos CAPI no mesmo painel.

**Gate de saída F5:** demonstração ponta a ponta com dados reais de 1 workspace piloto: campanha CTWA ativa, conversas atribuídas, QualifiedLead/Purchase devolvidos à Meta, ROAS real no painel.

---

## 7. Diretrizes de execução (invioláveis, ordem do proprietário 23/07/2026)

1. **Nenhum erro é pulado, silenciado ou contornado.** Teste vermelho, exceção nova, warning de migração ou comportamento estranho: parar, diagnosticar a causa raiz, corrigir e só então seguir. É proibido marcar como skip, comentar teste, engolir exceção com `except` genérico ou "resolver depois". Se a solução não for óbvia, investigar até achar; se exigir decisão de produto, levar ao proprietário com o diagnóstico pronto, nunca com o problema varrido.
2. **Não desestruturar o sistema.** Ele é todo conectado (webhooks → eventos canônicos → orquestração → intents → workers → UI; RLS e gates atravessando tudo). Antes de mexer numa peça, mapear quem a consome (grep de chamadores, testes que a cobrem, telas que a exibem); mudanças em contratos (schemas, eventos, rotas) são versionadas ou acompanhadas da atualização de TODOS os consumidores no mesmo commit. Remoções só com a prova de órfão da auditoria de contrato e passando o gate anti-regressão.
3. **Todo ajuste fecha o circuito backend ↔ frontend, nos dois sentidos.** Mudou endpoint/schema: conferir e ajustar a tela que consome. Mudou tela: conferir que o backend entrega o dado de verdade (nada de UI decorativa). Critério de pronto de qualquer item: demonstrável NA INTERFACE, exercitado de ponta a ponta (Playwright ou verificação manual no navegador), nunca "o endpoint existe".
4. **UX first, mínimo de cliques.** Toda superfície nova nasce da pergunta "qual o menor caminho para o usuário leigo conseguir o resultado?". Padrões: defaults inteligentes em vez de formulário em branco; detecção automática de estado em vez de perguntar o que o sistema já sabe; erros em linguagem de gente com o próximo passo embutido; wizard só onde a Meta obriga fricção, e mesmo ali com progresso salvo e retomável. Meta prática: nenhuma tarefa recorrente do usuário a mais de 3 cliques da Home.
5. **É um SaaS: o banco é sagrado.** Toda mudança de schema via migration Alembic com downgrade testado; nunca alterar/dropar coluna com dado sem migração de dados explícita; RLS e isolamento por workspace preservados em toda tabela nova (fail-closed como padrão); dedupe keys e constraints de idempotência mantidos; backup antes de qualquer migração destrutiva; seeds e fixtures jamais se misturam a dados reais (selo de origem em tudo).
6. **Tudo é LOCAL até ordem em contrário.** Nada de AWS, nada de API externa real, nada de MCP conectado a serviço externo, nada de credencial real: os gates (`KLM_EXTERNAL_ACTIONS_ENABLED`, `KLM_AWS_ENABLED`, `KLM_MODEL_APIS_ENABLED`, `KLM_ONLINE_APPROVED`) permanecem fechados durante TODA a execução deste plano. Cada fase entrega o sistema 100% pronto e provado em simulação local; a subida para AWS e a conexão de APIs/MCPs acontecem somente após aprovação expressa do proprietário, quando ele declarar o sistema 100%.

*(diretrizes 7 e 8 adicionadas por ordem do proprietário em 23/07/2026, válidas para TODOS os sprints)*

7. **O painel do Super Admin enxerga tudo.** Toda capacidade nova entregue em qualquer sprint expõe seus dados operacionais na superfície de plataforma: KPIs, erros, logs, eventos de auditoria, custo e estado por workspace. Exemplos obrigatórios já mapeados: pausas automáticas de campanha (kill switch e política de erro Meta) com motivo; negações do Meta Policy Gate por razão; tier/quality e saúde dos números por tenant; eventos de funil/tracking e recibos CAPI; falhas de webhook; consumo de IA. Critério de pronto inclui: "o Super Admin consegue ver isso sem acessar o banco?". Aproveitar e dar vida aos endpoints de plataforma hoje órfãos (mrr, token-usage, aws-costs) na mesma passada.

8. **UI encanta, e nunca se subtrai.** A interface é o que faz o cliente fechar contrato: cada sprint acrescenta, otimiza e melhora a experiência, sem remover funções ou telas existentes. Onde o plano previa "remover/aposentar" (gateway QR, pilha legada do MetaChannelStudio, wrapper legado, rotas duplicadas), a execução converte para: desligar por flag default-off, ocultar da navegação ou consolidar SEM apagar, e a remoção definitiva só acontece com aprovação expressa do proprietário item a item. Polimento visual (microinterações, estados vazios bonitos, feedback imediato, loading states) é trabalho de primeira classe, não sobra de sprint.

## 8. Governança de execução

**Política de modelos (aprovada 23/07/2026):** agentes EXECUTORES de tarefas com spec detalhada rodam em modelo Sonnet-class; agentes de REVISÃO adversarial, diagnóstico de causa raiz aberta e a sessão orquestradora usam modelo topo de linha (Fable/Opus). O investimento em modelo forte mora na revisão e no diagnóstico, não na digitação.

- **Ordem:** F1 e F2 em paralelo (equipes/trilhas distintas: motor × dados) → F3 → F4 → F5. Total estimado: **7 a 9 semanas**.
- **Cada fase**: branch próprio, revisão em 2 passes (executor + revisor independente), bateria completa do baseline verde (pytest/Vitest/tsc/build/Playwright/verify_klm_local), reprocessamento do agente auditor correspondente (`.claude/agents/meta-*`) como verificação independente, e gate de aprovação do proprietário para ligar flags externas.
- **Flags por fase** (nunca em bloco): F1 liga `KLM_MODEL_APIS`? não; liga WhatsApp real somente após W1 completo; F2 liga CAPI dispatch; F3/F4 ligam canais sociais; F5 liga Marketing API write.
- **Riscos registrados:** (1) App Review da Meta é serial e demorado: iniciar Business Verification e submissões na F1, não na F3; (2) prazo fatal v20.0 em 24/09/2026 independe do resto: corrigir na primeira semana; (3) datasets/CAPI exigem decisão dataset-por-workspace × dataset-da-plataforma antes do T3; (4) volume de trabalho de frontend é ~40% do plano: não deixar para o fim, cada trilha entrega backend + UI juntos.
- **Critério global de "limpo e sem erros"**: zero retry cego (todo erro Meta tem política), zero envio fora de janela (Policy Gate), zero promessa de UI sem lastro (matriz da seção 9 zerada), dedup total no CAPI, kill switches armados, e os 4 vereditos da auditoria de 23/07 virando **SIM**.

## 9. Matriz "nada dormente" (consolidada dos 3 confrontos)

| Item dormente/sem lastro | Estado hoje | Fase que resolve |
|---|---|---|
| Embedded Signup do workspace sem UI | endpoints prontos, tela read-only | F1-W5.1 |
| Onboarding guiado com passo a passo e prints reais (Tech Provider + tenant) | inexistente nas duas jornadas | F1-W6 (WhatsApp), F3-I2/I5 (IG), F4 (Página) |
| Motivo de erro de envio invisível | persistido, UI só ícone | F1-W5.2 |
| Janela 24h invisível (WA e social) | calculada (WA dispatcher) / não exposta | F1-W5.3 e F3-I5 |
| Categoria/quality de template não renderizados | no tipo, nunca exibidos | F1-W5.5 |
| Custo pré-disparo escondido (`finance:view`) | painel existe | F1-W5.5 |
| Drill-down por destinatário ausente | tipo pronto | F1-W5.4 |
| Tier/quality do número inexistente ponta a ponta | nem backend | F1-W5.6 |
| Biblioteca de templates ambígua (não é Meta) | induz a erro | F1-W2.3 |
| ctwa_clid: 0 ocorrências (back e front) | payload sobrevive em evidência | F2-T1/T5 |
| Spend só por fixture hardcoded | sem entrada real | F2-T4/T5.1 |
| Config Pixel/dataset/token CAPI inexistente | só webhook leadgen | F2-T5.2 |
| Feedback CAPI só "preparar", recibos sempre 0 | UI pronta, produtor ausente | F2-T3/T5.3 |
| Atribuição manual limitada a leadSource | campaignAttribution read-only | F2-T5.5 |
| Dashboard não distingue seed × real | só aba Captação distingue | F2-T5.6 |
| Google Ads fora do catálogo de provedores | serviço morto | F2-T3.5/T5.2 |
| rate_limits/quiet_hours gravados e ignorados | UI hardcoded, sem enforcement | F3-I1.3/I5 |
| Capability matrix legada all-true | decorativa | F3-I5 / F4 |
| Inbox `MetaChannelStudio` dormente (nunca renderiza) | 290 linhas mortas | F3-I5 (remoção) |
| Duas pilhas sociais concorrentes | canônica × legada | F3-I5 / F4 |
| Broadcast Messenger/IG prometido sem engine | form manual | F3-I1.6 / F4.3 |
| OAuth social inexistente (cadastro manual) | tokens nunca populados | F3-I2 / F4.4 |
| HUMAN_AGENT inexistente | lacuna | F4.2 |

---

*Rastreabilidade: relatórios integrais dos 7 auditores de canal + 3 auditores de frontend na sessão de 23/07/2026. Fontes oficiais: developers.facebook.com (Cloud API, messaging-limits, pricing, CAPI business-messaging/Dataset API, private replies, policy overview, changelog Messenger), confirmadas via fetch na data.*

---

## 10. ONDA B — Centro de Comando da Plataforma + UX de Permissões (ordem do proprietário, 23/07)

### B1. Super Admin "Centro de Comando" (substitui o painel atual por algo muito superior, sem remover nada)

**Estrutura**: visão executiva no topo (filtro de período global 7/30/90d, deltas vs período anterior, faixa de alertas acionáveis) + 4 domínios: **Negócio · Financeiro · Operação/Risco · Pessoas**, com navegação secundária.

**Financeiro completo (o coração)**:
- Receita: MRR/ARR, receita por plano, evolução (gráfico), churn e LTV estimado.
- **Assinaturas**: lista completa por workspace (plano, status: trial/ativa/inadimplente/pausada/cancelada, início, próxima cobrança, valor), com AÇÕES: mudar plano, aplicar desconto/cortesia, estender trial, suspender/reativar (tudo auditado e local-first: modelo de assinatura próprio consolidando o que existir de billing/entitlements + migration com downgrade se necessário).
- Margem por conta: consumo de IA (tokens/custo) vs cota do plano, custo estimado WhatsApp, e o P&L por workspace.
- Faturas/cobranças (registro local nesta fase; Stripe liga depois pelo webhook já existente).

**Pessoas (gestão macro de usuários)**: todos os usuários de todos os workspaces com busca global, papel/pacote, último acesso, status; ações: convidar, bloquear/desbloquear, resetar acesso, trocar pacote, e o "entrar como suporte" já existente por linha.

**Workspace 360**: drawer completo por tenant (assinatura, consumo, saúde Meta, erros recentes, usuários, ações rápidas) — o fim de pular entre abas.

**Operação/Risco**: o meta-health existente + falhas de webhook esgotadas + supressões + filas/beat + templates rejeitados (do approval loop).

### B2. UX coerente de permissões (o sistema nunca fica "estranho" para quem tem menos acesso)

**Princípio dos 3 estados** via componente único `PermissionGate` (variantes `hide | lock | readonly`), aplicado numa varredura por todas as páginas substituindo os padrões ad-hoc atuais (sumir/congelar):
1. **Sem acesso ao módulo** → item some da navegação (como hoje), mas a rota direta mostra empty-state elegante "Este módulo não faz parte do seu acesso" + botão "Solicitar acesso" (notifica o administrador do workspace).
2. **Vê mas não edita** → nunca "congelado mudo": badge/cadeado discreto no bloco + tooltip padrão "Somente leitura — seu pacote (X) não inclui edição; fale com o administrador"; botões de ação trocados por estado desabilitado COM explicação, nunca sumidos.
3. **Ação pontual bloqueada** → botão visível com cadeado + tooltip do papel que pode.
Complementos: legenda do próprio pacote na página de perfil ("O que meu acesso inclui", gerada do catálogo de pacotes), grupos de navegação nunca ficam vazios, e testes por papel (renderizar cada página-chave com cada pacote e validar que nada quebra nem some sem explicação).

**Sequenciamento**: B1 e B2 partem quando approval-loop liberar platform_service/PlatformDashboard e a Onda A liberar as páginas. B1 pode exigir migrations (assinaturas): permitidas com downgrade testado, padrão F1.3.
