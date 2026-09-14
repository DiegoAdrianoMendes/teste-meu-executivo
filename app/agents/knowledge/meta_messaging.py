"""Base de conhecimento condensada de mensageria Meta (estado 07/2026).

Extraída dos dossiês sênior em .claude/agents/meta-*.md (WhatsApp, Messenger,
Instagram e tracking/atribuição). Fonte única para os agentes nativos de
atendimento e disparos: nada aqui foi inventado; cada fato veio dos dossiês,
que citam a doc oficial da Meta. Revalidar na doc antes de mudar comportamento
em produção — a Meta muda regras várias vezes por ano.
"""

META_MESSAGING_KNOWLEDGE_VERSION = "meta-messaging-2026-07"

META_MESSAGING_KNOWLEDGE = """
=== WHATSAPP CLOUD API (07/2026) ===
- Caminho do Meu Executivo: app próprio (Tech Provider) + WABA dos clientes via
  Embedded Signup (popup OAuth gera token do System User do cliente). Exige
  Business Verification + App Review (whatsapp_business_messaging e
  whatsapp_business_management). On-Premises API está MORTA (sunset 23/10/2025).
- Técnica: WABA -> phone number -> POST /{phone_number_id}/messages; webhook com
  validação X-Hub-Signature-256 (HMAC do App Secret); mídia via URLs
  temporárias; interativas (botões, listas), WhatsApp Flows; throughput 80 mps
  auto-escalável até 1.000 mps.
- Cobrança POR MENSAGEM DE TEMPLATE (2025-2026): service = grátis; no BR,
  utility ~R$ 0,04-0,05 (grátis dentro de janela aberta), auth ~R$ 0,15-0,19,
  marketing ~R$ 0,31-0,38. Categoria errada na aprovação = custo até 7x maior.
- Fora da janela de 24h só template aprovado. Tiers por número: 250/1.000
  conversas iniciadas/24h -> 10k -> 100k -> ilimitado; escalada por volume +
  qualidade (janelas de 7 dias). Quality rating verde/amarelo/vermelho;
  vermelho bloqueia subida de tier.
- Erros que o motor de campanha DEVE tratar: 131047 (janela fechada -> exige
  template), 131049 (cap de marketing do destinatário -> skip silencioso,
  nunca retry), 131050 (opt-out dessa empresa -> registrar permanente),
  130429 (throughput -> backoff), 132000/132001 (template inexistente/não
  aprovado), 131026 (sem WhatsApp), 368/131031 (conta restrita -> PARAR a
  campanha imediatamente).
- Sobrevivência do número: rampa de aquecimento respeitando tiers, opt-in
  comprovado e auditável (data, origem, prova), frequency cap próprio por
  contato, horário comercial do fuso do destinatário, kill switch por failure
  rate/blocks, idempotência por mensagem em retries, pacing da Meta no ETA.

- GROUPS API (novidade 2026): conta OBA pode criar/gerenciar grupos
  programaticamente — até 8 membros por grupo, até 10.000 grupos por número,
  entrada SOMENTE por link de convite; mensagens via Messages API com
  recipient_type=group (texto, mídia e templates); webhooks de eventos de
  grupo; precificação por mensagem; Calling API não suportada em grupos;
  indisponível para números do app Business e Multi-solution.

=== MESSENGER (07/2026 — mudou drasticamente) ===
- Send API POST /{page-id}/messages com Page Access Token; webhooks do objeto
  page (messages, messaging_postbacks, message_deliveries); permissão
  pages_messaging com Advanced Access via App Review; handover protocol.
- Janela de 24h para resposta livre. DEPRECAÇÕES: Message Tags
  CONFIRMED_EVENT_UPDATE, ACCOUNT_UPDATE e POST_PURCHASE_UPDATE mortas em
  27/04/2026 (erro 100, mundial). Recurring/Marketing Messages mortas em
  10/02/2026 fora de AU/UE/JP/KR/UK — no Brasil NÃO existem mais.
- HUMAN_AGENT continua válida: 7 dias após a última mensagem do usuário,
  EXCLUSIVAMENTE suporte humano, nunca promoção, nunca acionada por bot.
- Consequência: no BR, Messenger é canal de ATENDIMENTO (inbound + 24h +
  HUMAN_AGENT). Broadcast orgânico = violação de política. Fora da janela o
  caminho é pago: Sponsored Messages / Click-to-Messenger Ads (Marketing API).
  Cadência que expira a janela degrada para outro canal (WhatsApp/e-mail).

=== INSTAGRAM MESSAGING (07/2026) ===
- Dois caminhos: Instagram Login direto (instagram_business_manage_messages)
  ou via Página do Facebook (legado suportado).
- DM só para quem INICIOU contato (mensagem, story reply, mention, comentário
  via private reply). Janela de 24h; depois HUMAN_AGENT por até 7 dias
  (suporte humano apenas).
- Caps duros: ~200 mensagens automatizadas/hora por conta; 1 automação por
  gatilho por usuário; 1 private reply por comentário. Estourar = bloqueio.
- NÃO existe broadcast frio nem template estilo WhatsApp. O "disparo"
  compliant é gatilho de inbound: comment-to-DM (comentário com palavra-chave
  -> webhook -> private reply -> janela 24h -> qualificação), story
  replies/mentions como lead quente, Click-to-Instagram-Direct Ads como única
  porta lícita de tráfego frio.
- Violações a bloquear: fila de DM para lista sem gatilho recente, HUMAN_AGENT
  para marketing, múltiplas private replies por comentário, automação fora da
  API oficial.

=== TRACKING E ATRIBUIÇÃO (anúncio -> conversa -> contrato) ===
- CTWA: a PRIMEIRA mensagem inbound traz referral object com ctwa_clid,
  source_url, source_id (ad id), source_type e criativo. Captura passiva, mas
  se o webhook não PERSISTIR o referral na hora, o dado se perde para sempre.
- Fechamento do loop: eventos de conversão (lead qualificado, reunião,
  contrato) via Conversions API for Business Messaging com ctwa_clid +
  action_source=business_messaging para o dataset no Events Manager.
  Advantage+ pune funil sem esses eventos (aprendizado pior, CPL maior).
- Messenger/Instagram: refs m.me/ig.me, postbacks e contexto de anúncio nos
  webhooks — persistir na entrada, mapear ao lead.
- CAPI: identificadores (telefone/e-mail) em SHA-256; dedupe Pixel x CAPI por
  event_id; conversões tardias como offline/CRM events com event_time
  retroativo. No Google, o equivalente é gclid + Data Manager API.
- Disciplina de UTMs por tenant persistida no LEAD, não só na sessão web.

=== GOVERNANÇA (inegociável) ===
- Agentes consultivos e read-only por padrão. Envio real ou mutação em conta
  Meta é ação externa: gates (external_delivery_allowed) + aprovação humana.
- Janela, caps e políticas Meta NUNCA são violados: pedido que implique
  violação (broadcast frio no Instagram, tag deprecada no Messenger) é
  RECUSADO com explicação da alternativa compliant.
- Nunca inventar IDs, métricas ou custos: dados de conta só via integração
  conectada; sem conta conectada, entregar o plano e o que falta.
"""

META_MESSAGING_KNOWLEDGE_SOURCES = [
    "https://developers.facebook.com/docs/whatsapp/cloud-api",
    "https://developers.facebook.com/documentation/business-messaging/whatsapp/groups",
    "https://developers.facebook.com/docs/whatsapp/embedded-signup",
    "https://developers.facebook.com/docs/whatsapp/messaging-limits",
    "https://developers.facebook.com/docs/whatsapp/pricing",
    "https://developers.facebook.com/docs/whatsapp/message-templates/guidelines",
    "https://developers.facebook.com/docs/messenger-platform/policy/policy-overview",
    "https://developers.facebook.com/docs/messenger-platform/send-messages/message-tags",
    "https://developers.facebook.com/docs/messenger-platform/handover-protocol",
    "https://developers.facebook.com/docs/messenger-platform/instagram/features/private-replies",
    "https://developers.facebook.com/docs/messenger-platform/instagram/webhooks",
    "https://developers.facebook.com/docs/marketing-api/conversions-api/business-messaging",
    "https://developers.facebook.com/docs/marketing-api/sponsored-messages",
]
