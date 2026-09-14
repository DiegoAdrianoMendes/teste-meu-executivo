# Benchmark Omnichannel KLM Hub V7

**Data da revisao:** 20 de julho de 2026  
**Fontes primarias:** paginas oficiais Kommo, Meta, TikTok, Lovable e materiais
oficiais ja registrados no benchmark V6  
**Evidencia de UX:** 26 capturas fornecidas pelo proprietario, incluindo Helena,
Kommo e interfaces de CRM/atendimento  
**Escopo:** produto local; nenhuma conexao externa real esta autorizada

Este documento complementa e prevalece sobre o
[`BENCHMARK_OMNICHANNEL_KOMMO_HELENA_V6_2026-07-20.md`](./BENCHMARK_OMNICHANNEL_KOMMO_HELENA_V6_2026-07-20.md)
quanto ao inventario competitivo e aos gates de implementacao. As regras de
evidencia e os limites de claims do V6 permanecem validos.

## 1. Conclusao executiva

Kommo e Helena nao vencem apenas por quantidade de integracoes. Eles reduzem o
numero de cliques entre entrada, resposta, qualificacao, tarefa, agenda,
transferencia e fechamento. Para superar esse patamar, o KLM precisa combinar:

1. inbox realmente unificada;
2. CRM e Caso 360 na mesma superficie operacional;
3. automacao social equivalente ao padrao ManyChat;
4. IA acionavel, com confirmacao e proveniencia;
5. roteamento honesto por capacidade, janela, custo e saude do canal;
6. isolamento juridico entre casos da mesma pessoa;
7. atribuicao completa da campanha ate contrato e receita;
8. operacao one-click no desktop e no mobile.

O principal diferencial defensavel do KLM nao e copiar o CRM generico. E
operar atendimento, comercial e caso juridico numa mesma experiencia, com
controle de audiencia, documentos, estimativa economica, RAG por caso,
aprovacao humana e trilha de auditoria.

## 2. Fontes oficiais verificadas

- [Kommo - produto e IA](https://www.kommo.com/br/)
- [Kommo - WhatsApp](https://www.kommo.com/br/whatsapp/)
- [Kommo - Instagram](https://www.kommo.com/br/instagram/)
- [Kommo - TikTok](https://www.kommo.com/br/tiktok/)
- [Kommo - integracoes](https://www.kommo.com/br/integracoes/)
- [Helena CRM](https://www.helenacrm.com/)
- [WhatsApp Business Platform](https://developers.facebook.com/docs/whatsapp/)
- [Instagram Platform](https://developers.facebook.com/docs/instagram-platform/)
- [Messenger Platform](https://developers.facebook.com/docs/messenger-platform/)
- [Meta Marketing APIs](https://developers.facebook.com/docs/marketing-apis/)
- [TikTok API for Business](https://business-api.tiktok.com/portal/docs)
- [Lovable integrations](https://docs.lovable.dev/integrations/introduction)

As paginas de produto provam o comportamento comercialmente anunciado. A
homologacao de cada capacidade ainda depende das permissoes, regioes, revisoes e
limites documentados pelos providers.

## 3. Inventario Kommo transformado em requisito

### 3.1 Inbox e operacao

| Capacidade observada | Requisito KLM V7 | Prova local obrigatoria |
|---|---|---|
| Caixa unica de WhatsApp, Instagram, Messenger e TikTok | feed canonico por pessoa, segmentado por caso | fixture de quatro canais aparece numa identidade sem misturar matters |
| Busca, favoritos e totalizadores | busca, estrela, nao lidos e contadores coerentes | E2E com atualizacao em tempo real |
| Filas Novos, Meus, Equipe e Outros | views operacionais persistidas e RBAC | dois usuarios e duas equipes com visibilidade distinta |
| Transferencia e conclusao | usuario, equipe, fila, takeover, concluir e reabrir | evento append-only e concorrencia testada |
| Consulta interna no mesmo chat | nota interna e conversa entre agentes | nunca serializar em payload destinado ao cliente |
| Multiplos numeros por conta e funil | policy por numero, equipe, fila, custo e saude | seletor mostra somente rotas elegiveis |
| Mensagem agendada | scheduler canonico com lease, retry e cancelamento | worker real local consome uma vez |
| Chamada no contexto | sessao de chamada capability-gated | estados e consentimento; sem alegar chamada externa |

### 3.2 WhatsApp

| Capacidade observada | Requisito KLM V7 |
|---|---|
| Cloud API e operacao hibrida | `cloud_api` e `coexistence` na mesma inbox, com transporte registrado por mensagem |
| Modelos aprovados | busca, preview, variaveis, categoria, idioma e elegibilidade |
| Campanhas/transmissao | segmentacao, opt-out, quiet hours, qualidade, budget, rate limit e reconciliacao |
| WhatsApp Flows | payload tipado, versao, resposta estruturada e vinculo ao card |
| Carrossel | composer e timeline para produto/servico juridico permitido pelo template |
| Lista | opcoes estruturadas para triagem, documentos e agenda |
| Botoes | respostas rapidas com postback idempotente |
| Widget de site | entry point first-party com UTM e click IDs |
| Click-to-WhatsApp/CAPI | ad, criativo, conversa, qualificacao, contrato e receita correlacionados |

### 3.3 Instagram e Messenger

| Capacidade observada | Requisito KLM V7 |
|---|---|
| DMs e mensagens com botao | mensagens tipadas e capability probe |
| Comentario publico | responder na publicacao sem confundir com DM |
| Comentario para DM | uma acao publica e uma privada independentes, com janela e receipt |
| Palavra-chave em comentario | trigger por conta, post, anuncio, palavra, regex segura e horario |
| Condicao de seguidor | branch do flow usando dado confirmado pelo provider |
| Mencao em Story | evento `story_mention` e resposta privada elegivel |
| Resposta a Story | evento `story_reply` na thread correta |
| Quebra-gelos | biblioteca versionada de icebreakers por playbook |
| Captura e qualificacao | campos mapeados para card, origem e proximo passo |

### 3.4 TikTok

| Capacidade observada | Requisito KLM V7 |
|---|---|
| Comentarios e mensagens na inbox | eventos sociais e DMs na mesma identidade |
| Responder e curtir comentarios | acoes capability-gated, idempotentes e auditadas |
| Palavra-chave | resposta publica, DM e flow configuraveis |
| Status de seguidor | ofertas/roteamento condicionais sem inferencia |
| Instant Forms | receipt assinado, mapping, card, thread e SLA |
| Atribuicao de anuncios | `ttclid`, campanha, grupo, anuncio, criativo e formulario |
| Quebra-gelos | prompts versionados e associados ao agente/playbook |
| IA 24/7 | autonomia graduavel, cadencia humana e handoff |

### 3.5 Copiloto e analytics

| Capacidade observada | Requisito KLM V7 |
|---|---|
| Resumo | resumo com fontes, data e caso ativo |
| Resposta sugerida | inserir rascunho editavel; nunca envio invisivel |
| Correcao e tom | corrigir, profissional, acolhedor, curto, longo e simples, com desfazer |
| Traducao | original preservado, idioma detectado e preferencia da pessoa |
| Perguntas sem resposta | pendencia com owner, SLA e gate opcional para concluir |
| Tarefa pela conversa | action intent com preview, confirmacao e resultado |
| Agenda pela conversa | disponibilidade, timezone, conflito, Meet/Teams e vinculo ao card |
| Lead quente | score explicavel por recencia, urgencia, aderencia e proximo passo |
| Pergunta sobre metricas | semantic layer autorizada, definicao, periodo e drill-down |
| Origem que mais converte | campanha ate receita, com custo e atribuicao explicitos |

### 3.6 Integracoes e captacao

O catalogo oficial da Kommo lista, entre outras capacidades, Facebook Ads,
Facebook Lead Forms, Google Ads, Google Analytics, TikTok Ads Manager, Gmail,
Google Agenda, telefonia, formulários, documentos e pagamentos. No KLM, o
equivalente deve ser incorporado como experiencia nativa e contrato first-party:

- Meta Lead Ads e lead ads com mensagens;
- TikTok Instant Forms;
- Google Ads e click IDs;
- formulario e chat do site Lovable;
- webhooks assinados e replay-safe;
- API publica versionada e chaves escopadas;
- eventos de conversao de volta ao provider apenas apos homologacao;
- pagina de conexoes com probe, permissoes, saude, limites e ultimo evento.

## 4. OCR funcional das capturas

As capturas adicionais confirmam os seguintes affordances que entram no
contrato de UX:

1. inbox em tres colunas: lista, conversa e assistente/contexto;
2. badges de canal no contato e em cada mensagem;
3. agente de IA identificado como autor;
4. troca automatica de idioma;
5. reescrita por gramatica, tom, tamanho e simplicidade;
6. sugestao de resposta que entra no rascunho;
7. tarefa inferida de data/hora com confirmacao;
8. detector de pergunta sem resposta;
9. disponibilidade de agenda consultada no contexto do chat;
10. origem TikTok Ads/Instagram visivel no card;
11. analytics conversacional por fonte de anuncio;
12. queues, transferencias, notas, anexos, modelos, pagamentos e scheduler nas
    capturas Helena;
13. ganho/perda, motivos, etapas, meta, funil e ciclo comercial nas capturas de
    CRM;
14. editor de agente com perfil, comportamento, conhecimento, configuracoes e
    habilidades.

## 5. Gaps do KLM detectados pela revisao independente

### P1 arquiteturais

1. identidade estava acoplada a um unico matter e podia reaproveitar thread
   errada quando a mesma pessoa possuia duas demandas;
2. mensagem canonica podia aparecer como enviada antes do receipt do provider;
3. ingressos Meta, TikTok e site ainda nao tinham uma fronteira publica assinada
   equivalente ao pipeline robusto do WhatsApp;
4. contexto omnichannel podia expor dados sem reaplicar `lead_data:view`;
5. mensagens agendadas V6 nao eram consumidas pelo worker canonico.

### P2 arquiteturais

- idempotencia concorrente sem recuperacao de conflito;
- acao social pouco vinculada ao evento, conta e alvo originais;
- FKs de conexao sem todas as garantias tenant-safe compostas;
- chamada capaz de avancar sem capability/consent/receipt;
- custos mutaveis, sem ledger reconciliado.

### P1/P2 de UX

- composer sem anexos e interativos completos;
- Copilot informativo, mas ainda pouco acionavel;
- mobile sem action sheet one-click;
- seletor de rota sem explicar janela, template, custo e saude;
- filtros e views operacionais ainda abaixo de Helena/Kommo.

Nenhuma dessas capacidades pode ser contada como pronta antes de implementacao,
teste e evidencia.

## 6. Vantagens que o KLM deve preservar

1. Caso 360 com documentos, contrato, procuracao, proposta, agenda, financeiro e
   historico.
2. Agentes juridicos especializados e playbook de vendas configuravel.
3. Projecao de honorarios e valor economico vinculada ao tratamento.
4. Visibilidade explicita do autor humano, advogado ou agente.
5. Nota interna, conversa entre agentes e aprovacao humana.
6. RAG isolado por workspace, pessoa e caso.
7. CRM Agent por comandos tipados e auditados.
8. Gate externo fechado e simulacao local honesta.

## 7. Posicionamento desejado

O KLM deve ser mais simples que um CRM generico para o fluxo diario, mais
profundo que uma inbox de WhatsApp para o caso juridico e mais seguro que uma
automacao social no-code para operacoes de alto impacto.

O claim local permitido ao final da V7 sera:

> O KLM Hub possui uma experiencia juridica omnichannel nativa, exercitada
> localmente de ponta a ponta, com identidade por pessoa, isolamento por caso,
> canais sociais, comentarios, formularios de anuncios, automacoes, equipes,
> mensagens programadas, chamadas modeladas, copiloto acionavel, atribuicao e
> analytics, pronta para a etapa separada de homologacao das APIs oficiais.

Nao se pode afirmar conexao, envio, entrega, chamada ou conversao real antes da
homologacao externa.
