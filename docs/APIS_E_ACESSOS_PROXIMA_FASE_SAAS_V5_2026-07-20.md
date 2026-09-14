# APIs e acessos da próxima fase do KLM Hub

**Status:** `NAO_AUTORIZADO_NESTA_FASE`  
**Objetivo:** preparar a homologação local conectada que ocorrerá depois da
aprovação do SaaS V5 e antes de qualquer atividade na AWS.

Nenhuma credencial deve ser enviada, salva ou ativada enquanto o manifesto P5
local não estiver aprovado. A conexão será gradual, um provider por vez, com
sandbox quando disponível, menor escopo possível, segredo fora do repositório,
webhook assinado, kill switch e rollback testado.

## 1. Ordem de homologação

1. **Modelo de IA:** providers candidatos por classe de tarefa, com limites de
   gasto, telemetria e avaliação cega. Não usar uma chave única sem subconta ou
   projeto dedicado quando o provider permitir segregação.
2. **Meta WhatsApp Cloud API:** Business Manager, WABA, número de teste/produção,
   app, token de sistema, verify token, app secret, templates e webhook.
3. **Agenda e reunião:** Google Calendar OAuth e, se necessário, Microsoft Graph
   para Outlook/Teams; calendários, escopos e política de disponibilidade.
4. **Documentos:** Google Drive/Docs com pasta dedicada e ZapSign para contrato e
   procuração; templates, signatários, webhook e regras de aprovação.
5. **Financeiro:** Asaas como primeira opção operacional ou Stripe conforme a
   decisão do escritório; conta sandbox, webhooks, meios de pagamento e
   reconciliação. Billing do SaaS permanece separado do financeiro jurídico.
6. **E-mail:** conta/domínio dedicado, Gmail API ou provedor transacional e
   ingestão por webhook/IMAP controlado; threading, anexos e identidade do cliente.
7. **Áudio:** ElevenLabs somente quando o lead insistir em voz; voz aprovada,
   limites, consentimento operacional, fallback textual e custo por minuto.
8. **Marketing:** Google Ads, Meta Ads/Pixel e TikTok Ads/Pixel; contas,
   conversion APIs, eventos, UTMs e taxonomia única de campanha.
9. **Observabilidade externa:** error tracking, uptime e alertas somente no
   ambiente de homologação. AWS continua sendo uma decisão posterior.

## 2. Matriz mínima por integração

| Integração | Materiais a fornecer depois | Escopos mínimos | Prova de saída |
|---|---|---|---|
| OpenAI/Anthropic/Google ou outro modelo | projeto/subconta, chave restrita e teto mensal | inferência apenas; sem treinamento por padrão | avaliação por tarefa, custo/tokens, p95, circuit breaker e zero chamada fora do gateway |
| Meta WhatsApp | WABA, phone number ID, app ID/secret, token e verify token | mensagens, mídia, templates e webhook necessários | inbound/outbound, status, mídia, múltiplos números, retry, opt-out e reconciliação |
| Google Calendar | OAuth client, redirect URIs e calendário dedicado | eventos e disponibilidade estritamente necessários | booking, update/cancel, conflito, timezone, Meet e vínculo órfão/card |
| Microsoft Graph | tenant/app e consentimento de escopos mínimos | calendário e reunião on-line, se adotados | disponibilidade, evento e link Teams sem duplicação |
| Google Drive/Docs | OAuth/service account dedicada e pasta raiz | arquivos e documentos da pasta KLM | geração, versionamento, permissão, link, revogação e trilha |
| ZapSign | conta sandbox, token, templates e secret de webhook | documentos e assinaturas | contrato/procuração, signatários, status, replay e aprovação |
| Asaas/Stripe | conta sandbox, chave restrita e secret de webhook | cobrança, recebimento e conciliação | cobrança idempotente, baixa, estorno, falha, retry e separação SaaS/jurídico |
| E-mail | domínio/conta dedicada, OAuth ou chave transacional | envio, recebimento e anexos | threading no Caso 360, dedupe, malware gate e ausência de vazamento |
| ElevenLabs | projeto, chave restrita, voz e budget | TTS/voz estritamente necessários | acionamento sob insistência, handoff, legenda, custo e fallback textual |
| Google/Meta/TikTok Ads | contas de teste, IDs, tokens e pixels | leitura de campanha e eventos de conversão | atribuição deduplicada, consentimento configurado, CAC/CPL/ROAS e cross-channel |

## 3. Checklist obrigatório por provider

- owner e conta empresarial definidos;
- ambiente sandbox/teste identificado;
- contrato de dados e retenção revisado;
- escopos mínimos e expiração/rotação documentados;
- segredo salvo em cofre local de homologação, nunca em Git ou log;
- assinatura, timestamp e replay protection no webhook;
- idempotency key, receipt, retry, timeout e dead-letter testados;
- limites de gasto, volume e rate limit configurados;
- `ExternalActionIntent` aprovado antes do primeiro efeito real;
- kill switch por workspace/provider visível no KLM;
- reconciliação e rollback executados;
- auditoria identifica usuário/agente, caso, playbook e provider;
- teste de dois workspaces confirma isolamento;
- evidência diferencia `sandbox_verified`, `provider_verified` e
  `production_verified`.

## 4. Dados que o proprietário precisará decidir

| Decisão | Opções já suportadas pelo desenho |
|---|---|
| Fechamento | sempre reunião humana, IA com aprovação ou IA dentro de limites |
| Agenda | Google, Microsoft ou ambos; calendário por advogado/equipe |
| Assinatura | ZapSign como padrão inicial ou outro adapter homologado |
| Pagamento jurídico | Asaas ou Stripe; entrada, êxito e honorário intermediário |
| Modelos | provider por classe de tarefa, qualidade e budget |
| Voz | desligada, somente sob insistência ou liberada por playbook |
| Canais de marketing | Google primeiro; Meta/TikTok em remarketing ou aquisição |
| Retenção | políticas por conversa, documento, áudio, log e RAG |

## 5. Gate antes da AWS

A fase conectada termina somente quando cada provider escolhido tiver uma jornada
completa em sandbox/homologação, efeitos auditados, custos medidos, falhas
reconciliadas e zero vazamento entre workspaces. Depois disso será produzido um
novo inventário de segredos, recursos, SLOs e custos para a decisão de arquitetura
AWS. Este documento não autoriza provisionamento nem mudança online.
