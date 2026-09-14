# Benchmark Omnichannel KLM Hub V6

**Data da pesquisa:** 20 de julho de 2026  
**Foco:** Kommo, Helena CRM, Meta, TikTok, WhatsApp e Lovable  
**Escopo do KLM:** implementacao e prova exclusivamente locais; nenhuma API real
ou infraestrutura AWS esta autorizada nesta fase.

## 1. Regra de evidencia

Este benchmark separa quatro classes que nao podem ser confundidas:

1. `produto_declarado`: comportamento mostrado ou descrito pelo fornecedor;
2. `api_documentada`: comportamento e limite encontrado em documentacao oficial;
3. `klm_simulado_local`: fluxo KLM exercitado com adapter falso e sem rede;
4. `klm_homologado`: somente podera ser usado depois de credencial, sandbox,
   webhook, reconciliacao e prova de entrega reais.

As telas fornecidas pelo proprietario foram processadas por OCR local. Elas sao
evidencia de UX e posicionamento do Kommo e da Helena, nao prova isolada da
disponibilidade de uma API ou de que o recurso funciona em toda conta/regiao.

## 2. Fontes auditadas

### Kommo

- [Produto e IA](https://www.kommo.com/br/)
- [WhatsApp](https://www.kommo.com/br/whatsapp/)
- [Instagram](https://www.kommo.com/br/instagram/)
- [TikTok](https://www.kommo.com/br/tiktok/)
- [Catalogo de integracoes](https://www.kommo.com/br/integracoes/)

O material publico apresenta inbox unificada, multiplos numeros, consulta
interna no chat, templates e broadcast no WhatsApp, automacoes de comentarios e
DMs, formularios instantaneos, origem publicitaria no card, agente de IA,
resumos, respostas sugeridas, reescrita, traducao, tarefas, agenda e analytics
conversacional.

### Helena CRM

- [Atendimento no WhatsApp](https://www.helenacrm.com/sistema-de-atendimento-whatsapp)
- [CRM integrado ao WhatsApp](https://www.helenacrm.com/crm-integrado-com-whatsapp)
- [Mensagens agendadas](https://helenacrm.com/agendar-mensagem-no-whatsapp)

As telas fornecidas mostram filas `Novos/Meus/Outros`, transferencia, conclusao,
anexos, mensagens rapidas, modelos, notas, agendamento, pagamento, chatbot,
times, pipelines, ganho/perda, motivos de perda, formularios, webhooks, API,
telefonia, relatorios e configuracao de agentes.

### Plataformas oficiais

- A [colecao oficial da Meta para WhatsApp](https://www.postman.com/meta/whatsapp-business-platform/overview)
  documenta Cloud API e Management API.
- A [colecao oficial da Meta para respostas privadas no Instagram](https://www.postman.com/meta/instagram/request/23987686-189d7215-22b3-403f-b2f5-a46c7e66a514)
  limita a resposta privada inicial a uma mensagem, em ate sete dias do
  comentario; novos envios dependem da resposta do usuario e da janela aplicavel.
- A [documentacao oficial de Business Messaging do TikTok](https://business-api.tiktok.com/portal/bm-api/education-hub)
  descreve mensagens em tempo real, texto, imagem, posts e templates interativos.
- O [indice oficial da TikTok API for Business](https://business-api.tiktok.com/gateway/docs/index?doc_id=1740295890332674&identify_key=c0138ffadd90a955c1f0670a56fe348d1d40680b3c89461e09f78ed26785164b&language=ENGLISH)
  lista leitura, resposta, criacao, like, ocultacao e exclusao de comentarios em
  videos proprios, alem de mensagens e automacoes.
- O [TikTok Lead Generation](https://ads.tiktok.com/help/article/available-crm-integrations-tiktok-lead-generation?lang=en&redirected=1)
  oferece Custom API com webhooks para sincronizacao de leads em tempo real.
- A [Conversions API da Meta](https://www.facebook.com/business/help/AboutConversionsAPI)
  conecta eventos de site, CRM, app, offline e mensageria aos sistemas de
  mensuracao e otimizacao da Meta.
- Os [Lead Ads com mensagens da Meta](https://www.facebook.com/business/ads/ad-objectives/lead-generation/lead-ads-with-messaging)
  podem iniciar conversas em Messenger, Instagram ou WhatsApp e alimentar CRM.
- A [documentacao de integracoes do Lovable](https://docs.lovable.dev/integrations/introduction)
  confirma que apps Lovable podem chamar qualquer API; no KLM, a integracao sera
  um formulario/webhook versionado do proprio site, sem dependencia do runtime
  Lovable.

## 3. OCR funcional das telas Kommo

| Tela | Capacidade observada | Requisito KLM |
|---|---|---|
| Inbox + Copilot | WhatsApp, Instagram, TikTok e Messenger na mesma lista e timeline | um lead, uma conversa canonica, badges de origem e multiplos bindings |
| Idiomas | troca automatica de idioma para texto e voz | detectar idioma, manter preferencia, traduzir rascunho e preservar original |
| Reescrita | corrigir gramatica; profissional, amigavel, espirituoso; maior, menor, simples | menu de transformacao com diff, desfazer e playbook efetivo |
| Tarefa | extracao de data/hora da conversa | sugestao de tarefa/reuniao, confirmacao e vinculo ao card |
| Rascunho | resposta sugerida inserida, mas editavel | nunca enviar sugestao automaticamente em modo humano/copiloto |
| Lacunas | perguntas do cliente que ficaram sem resposta | detector por mensagem, fila de pendencias e bloqueio de encerramento opcional |
| Agenda | consulta de disponibilidade e reagendamento no chat | slots locais, timezone, conflito, vinculo ao card e futura sincronizacao externa |
| Pipeline | canal/anuncio visivel no card | origem, campanha, conjunto, anuncio, criativo, formulario e click IDs |
| Analytics | pergunta em linguagem natural e tabela exportavel | consultas apenas sobre metricas autorizadas, com definicao e proveniencia |
| Inbox | busca, total, favorito, canal, timeline e painel lateral de IA | UX em tres paineis responsivos, sem ocultar Caso 360 e operacao |

## 4. Matriz competitiva por jornada

| Jornada | Patamar Kommo/Helena | Estado anterior do KLM | Upgrade V6 obrigatorio |
|---|---|---|---|
| Identidade omnichannel | canais reunidos por contato | WhatsApp, Meta e SPA ainda possuem trilhas paralelas | identidade canonica, endpoints e bindings; merge manual seguro |
| Filas | Novos, Meus, Outros, nao lidos, time | lista unica com filtros juridicos | filas reais, ownership, estrela, prioridade, SLA e views salvas |
| Transferencia | usuario, equipe, fila e conclusao | responsavel humano e routing parcialmente em metadata | equipes/grupos persistidos e evento append-only de transferencia |
| Multiplos numeros | numero compartilhado e varios numeros/funis | conexoes WhatsApp existem | seletor de numero, capacidade, custo, qualidade e policy por numero |
| Cloud + Coexistence | uso comercial hibrido esperado pelo proprietario | tipos de conexao sem modo operacional completo | modo `cloud_api`/`coexistence`, probe, historico e roteamento observavel |
| Chamada | telefonia e audio aparecem nos concorrentes | audio e ElevenLabs planejados | sessao de chamada, permissao, estado, transcript, custo e handoff |
| Mensagem agendada | agenda mensagem/modelo | scheduler robusto, mas nao canonico | agendamento por thread, canal, janela, template, cancelamento e retry |
| Comentario -> DM | palavra-chave, resposta publica e privada | automacao social separada | evento social canonico, receipt, dedupe, flow e limites do provider |
| Moderacao | responder, curtir e gerenciar comentario | capacidade parcial Meta | resposta, like, hide/unhide quando permitido, fila e auditoria |
| Formularios de anuncio | Meta/TikTok forms para card | tracking e campanhas locais sem ingestao canonica completa | receipt, field mapping, identidade, card, thread, SLA e reconciliacao |
| Site/Lovable | formulario e botao de chat | site e API local existem | SDK/form webhook first-party, UTM/click IDs, assinatura e replay |
| Click-to-message | anuncio abre conversa e atribui conversao | tracking de origem parcial | entry point canonico e feedback de qualificacao/contrato ao Ads Manager |
| Copilot | resumo, resposta, rewrite, traducao e tarefas | insights e sugestoes dispersos | painel unico, comandos one-click e action intents auditaveis |
| Pergunta pendente | identifica pergunta nao respondida | sem gate completo | extracao, status, owner, resolucao e criterio de encerramento |
| CRM | chat move e atualiza card | CRM agent e funil existem | cada evento relevante atualiza card por comando idempotente e auditado |
| Analytics | aquisicao, atendimento e IA | muitos KPIs, definicoes heterogeneas | semantic layer, comparativos, drill-down e analytics conversacional |
| Pagamento | solicitar pagamento no chat | financeiro e cobranca locais | pedido local versionado; adapter externo somente apos aprovacao |

## 5. O que o KLM deve superar

O objetivo nao e copiar layout. O KLM deve preservar o que esses produtos fazem
bem e adicionar controles indispensaveis para advocacia:

1. **Contexto juridico verificavel:** Card/Caso 360 e conversa sao as duas fontes
   autorizadas; documento, contrato, procuracao, agenda, proposta, financeiro e
   RAG ficam vinculados ao mesmo caso.
2. **Visibilidade por audiencia:** comentario publico, mensagem ao cliente, nota
   interna e conversa entre agentes possuem tipos e permissoes diferentes.
3. **Proveniencia de IA:** toda sugestao mostra agente, playbook, fontes, modelo,
   confianca e motivo; editar e rejeitar gera sinal de qualidade.
4. **Autonomia graduavel:** `human_only`, `ai_triage_human_meeting`,
   `ai_close_guarded` e `ai_autonomous_supervised` sao politicas, nao toggles
   cosmeticos.
5. **Isolamento por tenant/caso:** busca, RAG, cache, arquivos, eventos, analytics
   e prompts nao cruzam workspace, cliente ou matter.
6. **Economia observavel:** custo de mensagem, template, ligacao, tokens e
   armazenamento e atribuido por workspace, canal, campanha, lead e contrato.
7. **Falha honesta:** a UI mostra capacidade indisponivel, janela fechada,
   template necessario, token expirado ou permissao ausente antes da tentativa.

## 6. Limites que moldam o produto

1. Identidade entre canais nao pode ser inferida apenas por nome. Merge automatico
   exige identificador verificado; casos ambiguos vao para revisao.
2. Instagram permite uma resposta privada inicial ao comentario nas condicoes
   documentadas. O KLM deve armazenar a janela e impedir repeticao indevida.
3. TikTok, Instagram, Messenger e WhatsApp variam por conta, regiao, permissao e
   revisao do app. O onboarding executa capability probe e grava o resultado.
4. Coexistence nao significa que qualquer mensagem manual possa ser roteada sem
   restricao ou custo. O seletor usa apenas capacidades confirmadas e registra o
   transporte realmente escolhido.
5. Disparo em massa nao e sinonimo de envio livre. Segmentacao, template,
   opt-out, limites, qualidade do numero, quiet hours e rate limit sao hard gates.
6. O site criado no Lovable nao sera fonte de verdade. Ele enviara um contrato
   versionado ao KLM; o KLM responde com receipt e mantem a atribuicao.

## 7. Prioridades derivadas

### P0

- identidade e thread canonicas;
- convergencia de todos os webhooks;
- times, filas, transferencias e mensagens agendadas;
- comments/DM/forms como eventos idempotentes;
- isolamento e auditabilidade;
- inbox realmente operavel.

### P1

- Copilot completo;
- analytics conversacional;
- chamada e Coexistence simulados por capacidade;
- attribution feedback para Meta/TikTok;
- UX responsiva e atalhos one-click.

### P2

- otimizacao assistida de campanhas;
- experimentos de comentario/DM;
- previsao de capacidade, no-show e conversao;
- biblioteca de integracoes plug-and-play.

## 8. Claim defensavel

Ao fim da V6 local, a afirmacao permitida sera:

> O KLM possui uma experiencia omnichannel juridica nativa e exercitada
> localmente, com uma conversa por lead, eventos sociais e de anuncios,
> automacoes, equipes, Copilot e analytics, pronta para homologacao controlada
> das APIs oficiais.

Nao serao permitidas, antes da homologacao, as afirmacoes `canal conectado`,
`mensagem entregue`, `Coexistence ativo`, `chamada validada`, `CAPI recebida pela
Meta`, `TikTok aprovado` ou `pronto para producao`.
