# Matriz canônica de webhooks Meta

Esta matriz é derivada e validada contra `META_WEBHOOK_REGISTRY`. `Código pronto` não equivale a assinatura ou prova externa. A coluna final descreve o alvo de aceite local depois dos testes; o estado corrente de cada conexão vem da API e nunca é inferido desta documentação.

| Campo | Contrato | Roteamento | Handler | Projeção/domínio | App | Blackbox | Permissão | Alerta/automação | Desejada | Evidência externa | Estado alvo pós-testes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| account_alerts | Meta v26 | WABA/conexão | handle_account_alerts | whatsapp_health | Central webhooks | Saúde Meta | integrations:view | crítico/approval_each_time | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| account_review_update | Meta v26 | WABA/conexão | handle_account_review_update | whatsapp_health | Central webhooks | Saúde Meta | integrations:view | warning/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| account_settings_update | Meta v26 | WABA/conexão | handle_account_settings_update | whatsapp_health | Configuração | WhatsApps oficiais | integrations:view | warning/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| account_update | Meta v26 | WABA/conexão | handle_account_update | whatsapp_health | Saúde | Saúde Meta | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| automatic_events | Meta v26 | WABA/conexão | handle_automatic_events | meta_automatic_event | Automações | Operações | integrations:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| business_capability_update | Meta v26 | WABA/conexão | handle_business_capability_update | whatsapp_health | Capacidades | Saúde Meta | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| business_status_update | Meta v26 | WABA/conexão | handle_business_status_update | whatsapp_health | Saúde | Incidentes | integrations:view | crítico/approval_each_time | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| business_username_updates | Meta v26 | WABA/conexão | handle_business_username_updates | whatsapp_health | Perfil | WhatsApps oficiais | integrations:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| calls | Meta v26 | WABA/número/call | handle_calls | omnichannel_call | Conversas/Chamadas | Diagnóstico | mensagens:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| flows | Meta v26 | WABA/flow | handle_flows | whatsapp_flow | Flows | WhatsApps oficiais | integrations:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| group_lifecycle_update | Meta v26 | WABA/grupo | handle_group_lifecycle_update | whatsapp_group | Grupos | Saúde Meta | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| group_participants_update | Meta v26 | WABA/grupo | handle_group_participants_update | whatsapp_group | Grupos | Diagnóstico | integrations:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| group_settings_update | Meta v26 | WABA/grupo | handle_group_settings_update | whatsapp_group | Grupos | Diagnóstico | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| group_status_update | Meta v26 | WABA/grupo | handle_group_status_update | whatsapp_group | Grupos | Saúde Meta | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| history | Meta v26 | WABA/número | handle_history | coexistence | Conversas | Diagnóstico | mensagens:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| message_echoes | Meta v26 | WABA/número/mensagem | handle_message_echoes | conversation | Conversas | Inbox | mensagens:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| message_template_components_update | Meta v26 | WABA/template | handle_message_template_components_update | message_template | Templates | Saúde Meta | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| message_template_quality_update | Meta v26 | WABA/template | handle_message_template_quality_update | message_template | Templates | Alertas Meta | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| message_template_status_update | Meta v26 | WABA/template | handle_message_template_status_update | message_template | Templates | Saúde Meta | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| messages | Meta v26 | WABA/número/mensagem | handle_messages | conversation | Conversas | Inbox | mensagens:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| messaging_handovers | Meta v26 | WABA/número | handle_messaging_handovers | conversation_routing | Conversas | Inbox | mensagens:view | warning/approval_each_time | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| partner_solutions | Meta v26 | WABA/parceiro | handle_partner_solutions | partner_solution | Integrações | Governança | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| payment_configuration_update | Meta v26 | WABA/pagamento | handle_payment_configuration_update | payment_capability | Pagamentos | Saúde Meta | integrations:view | crítico/approval_each_time | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| phone_number_name_update | Meta v26 | WABA/número | handle_phone_number_name_update | whatsapp_health | Conexões | WhatsApps oficiais | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| phone_number_quality_update | Meta v26 | WABA/número | handle_phone_number_quality_update | whatsapp_health | Saúde | Saúde Meta | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| security | Meta v26 | WABA/conexão | handle_security | security_event | Alertas | Incidentes | integrations:view | crítico/approval_each_time | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| smb_app_state_sync | Meta v26 | WABA/número | handle_smb_app_state_sync | coexistence | Coexistência | Saúde Meta | integrations:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| smb_message_echoes | Meta v26 | WABA/número/mensagem | handle_smb_message_echoes | coexistence | Conversas | Inbox | mensagens:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| standby | Meta v26 | WABA/número | handle_standby | conversation_routing | Conversas | Inbox | mensagens:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| template_category_update | Meta v26 | WABA/template | handle_template_category_update | message_template | Templates | Saúde Meta | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| template_correct_category_detection | Meta v26 | WABA/template | handle_template_correct_category_detection | message_template | Templates | Saúde Meta | integrations:view | warning/suggest_action | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| tracking_events | Meta v26 | WABA/tracking | handle_tracking_events | tracking_event | Rastreamento | Relatórios Meta | integrations:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |
| user_preferences | Meta v26 | WABA/usuário | handle_user_preferences | coexistence | Configurações | Diagnóstico | integrations:view | info/observe_only | sim | receipt sanitizado | locally_proved_waiting_provider_event |

## Limite de evidência

- `locally_proved_waiting_provider_event`: handler, persistência e UI têm prova local, mas a Meta ainda não entregou evento real.
- `subscribed_waiting_event`: a API/painel comprovou assinatura, sem evento observado.
- `externally_proved`: somente após receipt real autenticado e efeito/UI observados.
- Inelegibilidade, requisito manual ou recusa nunca são convertidos em sucesso.
