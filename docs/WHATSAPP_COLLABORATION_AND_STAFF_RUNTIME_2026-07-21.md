# KLM Hub: colaboração, grupos e disponibilidade

Status: implementação local e preparação de integração externa. Nenhuma credencial da Meta, envio real ou recurso de AWS é ativado por este documento.

## Contrato funcional

O KLM Hub mantém uma conversa canônica por lead e registra, no mesmo histórico:

- mensagens externas e internas;
- resposta com referência à mensagem original;
- menções estruturadas a usuários e equipes;
- participantes de grupos internos da conversa;
- mensagens agendadas;
- mudanças de escala, férias, feriados e exceções;
- mensagem de ausência e redirecionamento aprovado;
- eventos de entrega, edição, exclusão e cadeia de evidência.

Referências e menções são entidades estruturadas, vinculadas ao `workspace_id`, `thread_id` e à mensagem canônica. Metadados no corpo da mensagem existem apenas como fallback de reconciliação e não substituem a validação tenant-scoped.

## Resposta contextual oficial

Na Cloud API, uma resposta contextual usa `context.message_id` com o identificador real `wamid` da mensagem anterior. O KLM Hub resolve esse identificador somente quando a mensagem citada pertence ao mesmo workspace, à mesma conversa, ao canal WhatsApp e à conexão selecionada. Divergência bloqueia o envio.

Fonte primária: [Meta WhatsApp Business Platform, exemplo oficial no Postman](https://www.postman.com/meta/whatsapp-business-platform/request/73yi2uj/send-reply-to-text-message).

## Grupos internos e grupos oficiais

São capacidades diferentes e nunca devem ser apresentadas como equivalentes:

| Capacidade | Grupo interno KLM | Grupo oficial Cloud API |
| --- | --- | --- |
| Pessoas e equipes do escritório | Suportado e tenant-scoped | Não se aplica |
| Menções internas | Suportado | Não presumido |
| Auditoria de participação | Suportado | Suportado quando o webhook oficial estiver habilitado |
| Criação/gestão sem credencial externa | Suportado | Não |
| Número em Coexistência | Não interfere | Não suportado pela API de grupos |
| Aplicativo inicial de Developers | Contrato local disponível | Gate externo fechado até elegibilidade/permissões |
| OBA e aprovação posterior | Não interfere | Pré-requisito para ativação real |

Contrato oficial revisado:

- [Groups API](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups/)
- [Getting started](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups/get-started/)
- [Group messaging](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups/groups-messaging/)
- [Groups reference](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups/reference/)
- [Coexistência do WhatsApp Business App](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users/)

O gate oficial considera: OBA, convite, limite de 8 participantes, limite de 10.000 grupos por número, uma empresa Cloud API por grupo e indisponibilidade para Business App/Coexistência/Multi-solution. Mensagens de grupo usam `recipient_type: group` e `to: GROUP_ID`. Recursos não documentados para grupos, como resposta contextual e menção semântica, permanecem bloqueados.

## Disponibilidade e ausência

Uma configuração é aplicada atomicamente e inclui:

- fuso horário;
- grade semanal completa;
- férias, feriados e exceções;
- ativação imediata ou programada;
- texto de ausência;
- redirecionamento para usuário ou equipe;
- troca automática quando aprovada.

O colaborador pode solicitar alteração própria. Gestor ou master pode solicitar para subordinados. Ninguém aprova a própria solicitação. Mudanças que atingem gestor/master exigem aprovação do master da conta. A configuração efetiva só muda depois da decisão, com versão esperada e trilha append-only.

No runtime, a resolução deve ser fail-closed: não redirecionar entre workspaces, não criar loops e não repetir mensagem de ausência para o mesmo evento de indisponibilidade. Uma intervenção humana explícita permanece soberana e auditada.

## Estados de produto

- `local_ready`: domínio, UI, persistência e testes locais disponíveis.
- `developer_app_ready`: payloads, webhooks e gates preparados; ainda sem credenciais.
- `external_gate_open`: credenciais válidas e permissões mínimas verificadas.
- `oba_ready`: elegibilidade e permissões de grupos verificadas.
- `coexistence_limited`: número opera em Coexistência e capacidades incompatíveis ficam bloqueadas.

Nenhum estado local é prova de aprovação pela Meta, entrega externa ou admissibilidade jurídica automática. Ele prova somente o comportamento técnico verificado no ambiente local.
