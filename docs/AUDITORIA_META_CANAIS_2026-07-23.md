# Auditoria Meta Channels — WhatsApp, Messenger, Instagram e Tracking

**Data:** 23/07/2026 · **Autorizada pelo proprietário** · Executada por 7 agentes especializados (`.claude/agents/meta-*`) em modo read-only, sem credenciais, sem chamadas a APIs da Meta. Regras revalidadas na documentação oficial durante a execução. Complementa o QA de 19/07 e alimenta o plano V5 (`KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md`).

## 0. Os quatro vereditos obrigatórios

| Pergunta | Veredito |
|---|---|
| O motor de disparos WhatsApp pode ir a produção sem derrubar número? | **NÃO** (4 P0; passa a "com condições" após corrigi-los) |
| Existe caminho no código que envia fora da janela sem base de plataforma? | **NÃO** (Messenger/social 100% fail-closed, provado em 4 camadas) |
| O funil de captação Instagram é 100% gatilho-first? | **NÃO** (o desenho é; a garantia é circunstancial, depende do gate global, não da arquitetura) |
| O KLM prova ao cliente qual anúncio gerou qual contrato? | **NÃO** (parcial só para lead forms Meta/TikTok/site; CTWA e marcos de contrato/receita não emitem eventos) |

**Leitura executiva:** a fundação é acima da média (assinaturas HMAC fail-closed, dedupe multi-camada, RLS por tenant, gate externo global em todas as bordas, opt-in default-deny, worker com lease/fencing). Nenhuma violação ATIVA de política existe hoje porque nada externo sai. Os riscos estão em: (1) o único canal com transporte real (WhatsApp) é cego à semântica de erros e limites da Meta; (2) um único ponto de binding fraco cria risco cross-tenant quando os canais sociais ligarem; (3) o rastreamento não captura o dado que liga anúncio a contrato.

## 1. CRÍTICOS consolidados (ordem de ataque)

### C1. Gateway QR não-oficial ativo em produção (WhatsApp)
`app/services/whatsapp/qr_gateway_client.py` + strategy + rota `POST /connections/qr` (`endpoints/whatsapp.py:345-361`). Viola os Termos do WhatsApp Business (o código mapeia até status `"banned"` do gateway). Remover do caminho de produção; manter só Cloud API + Coexistence.

### C2. Motor de envio cego aos códigos de erro da Meta (WhatsApp)
Zero tratamento de 131047/131049/131050/131026/130429/132000/132001/368/131031 em todo o repo; `campaign_service.py:2128-2151` faz retry cego de qualquer exceção (até 3x), inclusive de quem pediu opt-out (131050) e de conta restrita (368). `meta_cloud_client.py:589-613` só classifica OAuth. Correção: mapa de erros → política por código (skip / opt-out permanente / backoff / parada total).

### C3. Cross-tenant: `entry.id` do payload nunca é validado contra a conexão (social)
`social_event_adapter_service.py:162`. A assinatura Meta é do app (um só para todos os tenants); evento da conta A entregue na URL do tenant B seria ingerido no workspace errado (DMs vazando entre escritórios). Vale para mensagens E comentários. Correção: rejeitar/quarentenar entry cujo `id` difira de `external_account_id` (obrigatório e não-nulo), e no go-live migrar para receptor único por app roteando por `entry.id`.

### C4. Tracking não captura `ctwa_clid` (a string não existe no repo)
`whatsapp/webhook_service.py:158-235` ignora `message.referral`. O clique de anúncio CTWA é descartado no ingest. Atenuante: payload bruto sobrevive em `MessageEvidence` → **backfill retroativo é possível**. Correção: extrair referral no inbound → `thread_metadata.acquisition` + `AcquisitionEvent(conversation_started, click_ids={ctwa_clid})` + job de backfill.

### C5. Marcos de dinheiro não emitem eventos de atribuição
Webhook ZapSign move o caso para "assinado" sem emitir `contract_created` (`webhooks.py:442-467`); pagamento (finance/stripe/local billing) nunca emite `payment_confirmed`; SDR cria Matter sem `lead_source` (`sdr_intake_service.py:276-287`). O motor de CAC/ROAS existe e fica vazio. Correção: 3 pontos de código sobre `record_acquisition_event` (já idempotente).

### C6. Prazo fatal 24/09/2026: `v20.0` hardcoded
`transcription_service.py:45` ignora `WHATSAPP_API_VERSION`; Graph v20 desativa em 24/09/2026 → transcrição de áudio/OCR do SDR morre em silêncio. Corrigir para a versão do settings (e subir default; validar Groups API na versão pinada).

### C7. Demais P0 do motor de campanhas WhatsApp
(a) Sem kill switch automático por failure rate/quality (campanha 100% falha roda até o fim); (b) envio não idempotente em crash do worker (commit tardio em `campaign_service.py:2060-2172` → reenvio de até 50 msgs; `delivery_uncertain` sofre retry cego); (c) zero consciência de tier/rampa/pacing (número novo com limite 250/24h estoura no 1º ciclo); (d) template `pending` pode ser lançado (`campaign_service.py:707-717`).

## 2. ALTOS (pré-go-live dos canais sociais e robustez WhatsApp)

**WhatsApp:** janela de 24h não gerida no envio livre (rastrear `last_inbound_at`, bloquear ou degradar para template); rotação de chave dos tokens (MultiFernet versionado; decrypt falho hoje vira `None` silencioso); webhook processa inline (mover para Celery, responder 200 imediato); campos `pricing` do status webhook descartados (alimentar o ledger BRL e o repasse); opt-out inbound não automatizado (SAIR/PARE/STOP → supressão); re-registro gera PIN novo (usar o armazenado; tratar 133005).

**Instagram (com módulo Comentários):** cap de ~200 msgs automatizadas/h POR CONTA não aplicado (contador é por workspace, teto configurável 1.000/h; `rate_limits`/`quiet_hours` das regras são gravados e NUNCA lidos — promessa vazia na UI de `/app/comentarios`); mais de 1 private reply por comentário quando várias regras casam (`stopOnMatch: false` default; dedup só dentro da regra); janela de 7 dias da private reply e regra de live não verificadas; **comentário público abre indevidamente a janela de 24h no modelo** (`last_inbound_at` atualizado por evento público — a janela só deve abrir com DM real); `serviceWindowHours` configurável até 168h (clamp de 24h para canais Meta); `MetaChannelCampaign` IG é broadcast-shaped e aceita `PATCH status=running` sem gate.

**Messenger:** enforcement da janela mora só no dispatcher WhatsApp (torná-lo canal-agnóstico no worker; agendamento aceita `channel=messenger` sem checar janela); campanhas com compliance autodeclarado (elegíveis/bloqueados digitados à mão → computar server-side de `last_inbound_at`); remover a palavra "broadcast" da UI do canal (`MetaChannelStudio.tsx:1131-1132`) — não existe broadcast orgânico no BR pós-2026; capability matrix declara `outbound_messages: supported` sem transporte nem nota de janela.

**Plataforma:** HUMAN_AGENT inexistente (modelar restrita a `author_type=human`, ≤7 dias, App Review própria); fluxo OAuth de onboarding social inexistente (tokens nunca populados; sem refresh de 60 dias); mapa App Review documentado no relatório IG: `instagram_business_basic` + `instagram_business_manage_messages` + `instagram_business_manage_comments` (Instagram Login) com Business Verification e Advanced Access.

## 3. MÉDIOS/ROADMAP

Referral social (`adId/postId`) capturado mas nunca vira `AcquisitionEvent`; `ref` de m.me/ig.me não lido; comentários de anúncios sem correlação de ad; SDR é WhatsApp-only (comment-to-DM não alimenta qualificação nem cria Matter); trilho legado "Studio" (`meta_channels_service.py:822-955`) automatiza sem dedup/janela/private reply → aposentar em favor do trilho v6; worker de CAPI inexistente (intents `ads_conversion_feedback` preparados e nunca consumidos) + variante `business_messaging`+`ctwa_clid` a criar; spend ausente (CAC/ROAS reais vazios) → ingest manual/CSV primeiro; dupla fonte de verdade de conversões (`reports_service` com heurística de substring vs motor de atribuição) → unificar; capability defaults otimistas em conexão não verificada; `follow_status` declarativo; story mention via `changes` não deveria abrir janela; loop de auto-resposta possível com `external_account_id` vazio (filtrar `direction=inbound` no runtime); `MetaAdsService`/`GoogleAdsService` seguem mortos (decisão registrada no plano: completar ou remover).

## 4. Pontos fortes confirmados pelos 7 auditores

Assinatura HMAC com `compare_digest` e fail-closed em todos os webhooks; dedupe por `wamid`/`external_event_id` com constraints; binding de tenant WhatsApp via SECURITY DEFINER + RLS; tokens/segredos cifrados com chave dedicada forte; gate externo global em 4 camadas com teste dedicado; opt-in default-deny com recálculo no launch; Embedded Signup multi-tenant completo; worker com lease/fencing/`delivery_uncertain`; réguas que degradam para WhatsApp/e-mail; zero código dependente das deprecações Meta de 2026; página de Comentários honesta (banner de bloqueio, RBAC, ações rotuladas); modelo canônico de atribuição multi-touch com dedupe e hashing prontos.

## 5. Plano de correção em ondas

**Onda A — antes de ligar qualquer API externa (WhatsApp + tracking + cross-tenant):**
C1 (remover QR), C2 (mapa de erros), C7a-d (kill switch, idempotência/claim durável, tier/rampa, só template approved), C6 (v20.0), C3 (entry.id), C4 (ctwa_clid + backfill), C5 (contract_created/payment_confirmed/lead_source). Critério de saída: motor de disparos passa a "com condições"; funil anúncio→contrato fecha ponta a ponta em simulação local.

**Onda B — pré-go-live dos canais sociais:**
IG: cap por conta ≤200/h aplicado no worker + rate_limits/quiet_hours das regras aplicados + dedup de private reply entre regras + janela 7d/live + clamp 24h + janela não abre por comentário público + guard-rail nas campanhas IG. Messenger: janela canal-agnóstica + campanhas server-side + copy sem "broadcast". Plataforma: OAuth + refresh de tokens + App Review + HUMAN_AGENT restrita a humano. Critério de saída: vereditos 2 e 3 viram "sim" por arquitetura, não por gate.

**Onda C — produto e otimização:**
Worker CAPI (business_messaging + ctwa_clid) atrás do gate; SDR social intake; correlação de ads em comentários; aposentar trilho legado Studio; spend ingest e CAC/ROAS reais; unificar reporting; reposicionar "campanhas" Messenger/IG como reengajamento em janela + mídia paga.

**Regra de condução:** cada onda fecha com a bateria completa do baseline (pytest/Vitest/tsc/build/Playwright/verify) verde + revisão em 2 passes, conforme V5. Gates externos permanecem fechados até aprovação expressa do proprietário por canal.

## 6. Rastreabilidade

Relatórios integrais dos 7 agentes emitidos em 23/07/2026 na sessão de auditoria autorizada (WhatsApp senior, WhatsApp disparos, Messenger senior, Messenger disparos, Instagram senior com seção de Comentários, Instagram disparos com seção de Automações de Comentários, Tracking/Atribuição). Definições dos agentes em `.claude/agents/meta-*.md` com links das fontes oficiais consultadas.
