# Cobertura dos campos de webhook Meta WhatsApp

Esta matriz é a fonte operacional da cobertura pedida a partir do painel Meta v26.0. Ela não afirma que um campo está assinado: assinatura é estado externo da Meta. O produto sempre exibe os 33 campos; um evento recebido muda apenas o estado de observação daquele campo.

## Contrato comum

- **Nativo:** além da evidência sanitizada, existe handler ou projeção determinística no domínio indicado.
- **Semântico:** o ingress aceita o campo, elimina segredos/conteúdo privado, deduplica, audita e o exibe no App e no Blackbox com orientação operacional. Não executa uma mutação de negócio por inferência.
- App: `Configurações > WhatsApp > Central de webhooks Meta`.
- Blackbox: `WhatsApp oficial > Central de webhooks Meta`.
- APIs: `GET /api/v1/whatsapp/meta-operations` e `GET /api/v1/platform/official-whatsapp/meta-operations`.

## Sprint 1 — 10 campos

| Campo | Suporte | Destino |
|---|---|---|
| `account_alerts` | Semântico | Saúde da conta |
| `account_review_update` | Semântico | Saúde da conta |
| `account_settings_update` | Semântico | Configuração da conta |
| `account_update` | Semântico | Saúde da conta |
| `automatic_events` | Semântico | Automações |
| `business_capability_update` | Semântico | Capacidades do canal |
| `business_status_update` | Semântico | Saúde da conta |
| `business_username_updates` | Semântico | Identidade do canal |
| `calls` | Semântico | Chamadas WhatsApp |
| `flows` | Semântico | WhatsApp Flows |

## Sprint 2 — 10 campos

| Campo | Suporte | Destino |
|---|---|---|
| `group_lifecycle_update` | Semântico | Grupos oficiais |
| `group_participants_update` | Semântico | Grupos oficiais |
| `group_settings_update` | Semântico | Grupos oficiais |
| `group_status_update` | Semântico | Grupos oficiais |
| `history` | Nativo | Conversas e histórico |
| `message_echoes` | Nativo | Conversas |
| `message_template_components_update` | Nativo | Templates Meta |
| `message_template_quality_update` | Nativo | Templates Meta |
| `message_template_status_update` | Nativo | Templates Meta |
| `messages` | Nativo | Conversas |

## Sprint 3 — 10 campos

| Campo | Suporte | Destino |
|---|---|---|
| `messaging_handovers` | Semântico | Roteamento de atendimento |
| `partner_solutions` | Semântico | Integrações e parceiros |
| `payment_configuration_update` | Semântico | Pagamentos no WhatsApp |
| `phone_number_name_update` | Nativo | Identidade do número |
| `phone_number_quality_update` | Nativo | Qualidade do número |
| `security` | Semântico | Segurança |
| `smb_app_state_sync` | Nativo | Coexistência |
| `smb_message_echoes` | Nativo | Conversas e coexistência |
| `standby` | Semântico | Roteamento de atendimento |
| `template_category_update` | Nativo | Templates Meta |

## Sprint 4 — 3 campos

| Campo | Suporte | Destino |
|---|---|---|
| `template_correct_category_detection` | Nativo | Templates Meta |
| `tracking_events` | Semântico | Métricas e tracking |
| `user_preferences` | Semântico | Coexistência e preferências |

## Segurança e estados honestos

- Corpos de mensagens permanecem na Inbox canônica e não são copiados para a auditoria de plataforma.
- Tokens, senhas, chaves e estruturas aninhadas não são persistidos nessa projeção.
- Ausência de evento aparece como `ainda não observado`, nunca como `desassinado`, `zero` ou `falhou`.
- Campo sem handler específico permanece visível como `semântico`; nenhuma mutação externa ou financeira ocorre automaticamente.
- O mesmo catálogo tipado alimenta App e Blackbox, evitando listas divergentes no frontend.
